"""Tagged-resource recovery and cleanup with synthetic EC2 responses only."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud
from test_real_world import AwsError, job


class CloudLifecycle(unittest.TestCase):
    def setUp(self):
        self.value = job(); self.node = self.value['nodes'][0]
        self.ec2 = MagicMock(); self.ssm = MagicMock()
        self.enterContext(patch.object(cloud, 'client', side_effect=lambda service, region: self.ec2 if service == 'ec2' else self.ssm))
        self.ec2.describe_vpcs.return_value = {'Vpcs': []}; self.ec2.create_vpc.return_value = {'Vpc': {'VpcId': 'vpc-synthetic', 'State': 'pending'}}
        self.ec2.describe_internet_gateways.return_value = {'InternetGateways': []}; self.ec2.create_internet_gateway.return_value = {'InternetGateway': {'InternetGatewayId': 'igw-synthetic'}}
        self.ec2.describe_route_tables.side_effect = lambda **options: {'RouteTables': [{'RouteTableId': 'rtb-synthetic', 'Routes': [], 'Associations': []}]} if 'RouteTableIds' in options else {'RouteTables': []}
        self.ec2.create_route_table.return_value = {'RouteTable': {'RouteTableId': 'rtb-synthetic', 'Routes': []}}
        self.ec2.describe_subnets.return_value = {'Subnets': []}; self.ec2.create_subnet.return_value = {'Subnet': {'SubnetId': 'subnet-synthetic'}}
        self.ec2.describe_security_groups.return_value = {'SecurityGroups': []}; self.ec2.create_security_group.return_value = {'GroupId': 'sg-synthetic'}
        self.ec2.describe_instances.return_value = {'Reservations': []}; self.ec2.run_instances.return_value = {'Instances': [{'InstanceId': 'i-synthetic'}]}
        self.ssm.get_parameter.return_value = {'Parameter': {'Value': 'ami-synthetic'}}

    def test_fresh_allocation_uses_scoped_tags_stable_tokens_and_only_the_recorded_placement(self):
        self.node['image_id'] = 'ami-retained'
        cloud.provision(self.value, self.node, 'synthetic-instance-profile')
        self.ssm.get_parameter.assert_not_called()
        self.ec2.get_waiter.assert_called_once_with('vpc_available')
        self.ec2.get_waiter.return_value.wait.assert_called_once_with(VpcIds=['vpc-synthetic'], WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
        self.ec2.create_route.assert_called_once_with(RouteTableId='rtb-synthetic', DestinationCidrBlock='0.0.0.0/0', GatewayId='igw-synthetic')
        self.ec2.attach_internet_gateway.assert_called_once_with(InternetGatewayId='igw-synthetic', VpcId='vpc-synthetic')
        self.ec2.associate_route_table.assert_called_once_with(RouteTableId='rtb-synthetic', SubnetId='subnet-synthetic')
        subnet = self.ec2.create_subnet.call_args.kwargs
        self.assertEqual(subnet['AvailabilityZoneId'], self.node['zone_id']); self.assertEqual(subnet['CidrBlock'], '10.253.0.0/24')
        launch = self.ec2.run_instances.call_args.kwargs
        self.assertEqual(launch['ImageId'], 'ami-retained'); self.assertEqual(launch['ClientToken'], 'job-1-server'); self.assertEqual(launch['IamInstanceProfile'], {'Arn': 'synthetic-instance-profile'})
        self.assertEqual(launch['MinCount'], 1); self.assertEqual(launch['MaxCount'], 1)
        self.assertEqual(launch['MetadataOptions'], {'HttpTokens': 'required', 'HttpPutResponseHopLimit': 1})
        self.assertEqual(launch['InstanceInitiatedShutdownBehavior'], 'terminate')
        self.assertIn('shutdown -h +60', launch['UserData'])
        self.assertEqual({row['ResourceType'] for row in launch['TagSpecifications']}, {'instance', 'volume'})
        for specification in launch['TagSpecifications']:
            tags = {row['Key']: row['Value'] for row in specification['Tags']}
            self.assertEqual(tags, dict(Project='JumpServeRealWorld', JobId='job-1', Name='jumpserve-real-server', ExpiresAt=str(self.value['deadline'])))
        self.assertEqual(self.node['state'], 'pending'); self.assertEqual(self.node['security_group'], 'sg-synthetic')

    def test_retry_adopts_every_preexisting_network_resource_and_never_creates_a_second_instance(self):
        self.ec2.describe_vpcs.return_value = {'Vpcs': [{'VpcId': 'vpc-synthetic', 'State': 'available'}]}
        self.ec2.describe_internet_gateways.return_value = {'InternetGateways': [{'InternetGatewayId': 'igw-synthetic', 'Attachments': [{'VpcId': 'vpc-synthetic'}]}]}
        self.ec2.describe_route_tables.side_effect = None; self.ec2.describe_route_tables.return_value = {'RouteTables': [{'RouteTableId': 'rtb-synthetic', 'Routes': [{'DestinationCidrBlock': '0.0.0.0/0'}], 'Associations': [{'SubnetId': 'subnet-synthetic'}]}]}
        self.ec2.describe_subnets.return_value = {'Subnets': [{'SubnetId': 'subnet-synthetic'}]}; self.ec2.describe_security_groups.return_value = {'SecurityGroups': [{'GroupId': 'sg-synthetic'}]}
        instance = {'InstanceId': 'i-original', 'ImageId': 'ami-original', 'InstanceType': 't3.medium', 'State': {'Name': 'running'}}
        self.ec2.describe_instances.return_value = {'Reservations': [{'Instances': [instance]}]}
        cloud.provision(self.value, self.node, 'synthetic-instance-profile')
        self.assertEqual(self.node['instance_id'], 'i-original'); self.assertEqual(self.node['image_id'], 'ami-original')
        for method in ['create_vpc', 'create_internet_gateway', 'create_route_table', 'create_subnet', 'create_security_group', 'run_instances', 'create_route', 'associate_route_table', 'attach_internet_gateway']:
            getattr(self.ec2, method).assert_not_called()
        self.ssm.get_parameter.assert_not_called()
        for state in ['terminated', 'shutting-down']:
            instance['State']['Name'] = state
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'terminated before launch'): cloud.provision(self.value, self.node, 'synthetic-instance-profile')
        self.ec2.run_instances.assert_not_called()

    def test_refresh_requires_both_running_state_and_public_address_and_retains_interruption(self):
        self.node['instance_id'] = 'i-original'
        instance = {'State': {'Name': 'pending'}}
        self.ec2.describe_instances.return_value = {'Reservations': [{'Instances': [instance]}]}
        self.assertFalse(cloud.refresh(self.node)); self.assertIsNone(self.node['public_ip'])
        instance['State']['Name'] = 'running'; self.assertFalse(cloud.refresh(self.node))
        instance['PublicIpAddress'] = '192.0.2.10'; self.assertTrue(cloud.refresh(self.node)); self.assertEqual(self.node['public_ip'], '192.0.2.10')
        for state in ['terminated', 'shutting-down', 'stopped', 'stopping']:
            instance['State']['Name'] = state
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'server unexpectedly entered ' + state): cloud.refresh(self.node)
            self.assertEqual(self.node['state'], state)
        self.ec2.describe_instances.assert_called_with(InstanceIds=['i-original'])

    def test_hub_admits_only_wireguard_spokes_and_reconciles_duplicate_ingress_without_ignoring_other_errors(self):
        for index, node in enumerate(self.value['nodes']): node.update(public_ip='192.0.2.' + str(index + 1), security_group='sg-' + str(index))
        hub = self.value['nodes'][1]
        self.ec2.authorize_security_group_ingress.side_effect = [AwsError('InvalidPermission.Duplicate'), {}, {}]
        cloud.allow_peers(self.value, hub)
        self.assertEqual(self.ec2.authorize_security_group_ingress.call_count, 3)
        ips = []
        for call in self.ec2.authorize_security_group_ingress.call_args_list:
            self.assertEqual(call.kwargs['GroupId'], 'sg-1'); rule = call.kwargs['IpPermissions'][0]
            self.assertEqual((rule['IpProtocol'], rule['FromPort'], rule['ToPort']), ('udp', 51820, 51820)); ips.extend(row['CidrIp'] for row in rule['IpRanges'])
        self.assertEqual(ips, ['192.0.2.1/32', '192.0.2.3/32', '192.0.2.4/32'])
        self.ec2.authorize_security_group_ingress.side_effect = AwsError('UnauthorizedOperation')
        with self.assertRaises(AwsError): cloud.allow_peers(self.value, hub)

    def test_cleanup_waits_for_every_instance_then_deletes_only_tagged_resources_in_dependency_order(self):
        self.ec2.describe_instances.return_value = {'Reservations': [{'Instances': [{'InstanceId': 'i-original', 'State': {'Name': 'shutting-down'}}, {'InstanceId': 'i-terminated', 'State': {'Name': 'terminated'}}]}]}
        self.assertFalse(cloud.cleanup_region(self.value, 'us-east-1'))
        self.ec2.terminate_instances.assert_called_once_with(InstanceIds=['i-original']); self.ec2.delete_subnet.assert_not_called()
        self.ec2.reset_mock()
        self.ec2.describe_instances.return_value = {'Reservations': [{'Instances': [{'InstanceId': 'i-original', 'State': {'Name': 'terminated'}}]}]}
        self.ec2.describe_subnets.return_value = {'Subnets': [{'SubnetId': 'subnet-original'}]}; self.ec2.describe_security_groups.return_value = {'SecurityGroups': [{'GroupId': 'sg-original'}]}
        self.ec2.describe_route_tables.side_effect = None; self.ec2.describe_route_tables.return_value = {'RouteTables': [{'RouteTableId': 'rtb-original', 'Associations': [{'Main': True, 'RouteTableAssociationId': 'main-untouched'}, {'Main': False, 'RouteTableAssociationId': 'rtbassoc-original'}]}]}
        self.ec2.describe_internet_gateways.return_value = {'InternetGateways': [{'InternetGatewayId': 'igw-original', 'Attachments': [{'VpcId': 'vpc-original'}]}]}
        self.ec2.describe_vpcs.return_value = {'Vpcs': [{'VpcId': 'vpc-original'}]}
        self.assertTrue(cloud.cleanup_region(self.value, 'us-east-1'))
        self.ec2.terminate_instances.assert_not_called(); self.ec2.disassociate_route_table.assert_called_once_with(AssociationId='rtbassoc-original')
        deletes = [call[0] for call in self.ec2.method_calls if call[0].startswith(('delete_', 'disassociate_', 'detach_'))]
        self.assertEqual(deletes, ['delete_subnet', 'delete_security_group', 'disassociate_route_table', 'delete_route_table', 'detach_internet_gateway', 'delete_internet_gateway', 'delete_vpc'])
        for call in self.ec2.method_calls:
            if call[0].startswith('describe_'): self.assertEqual(call.kwargs['Filters'], [{'Name': 'tag:Project', 'Values': ['JumpServeRealWorld']}, {'Name': 'tag:JobId', 'Values': ['job-1']}])

    def test_cleanup_empty_resources_is_idempotent_and_provider_errors_are_not_success(self):
        self.ec2.describe_route_tables.side_effect = None
        for method, key in [('describe_instances', 'Reservations'), ('describe_subnets', 'Subnets'), ('describe_security_groups', 'SecurityGroups'), ('describe_route_tables', 'RouteTables'), ('describe_internet_gateways', 'InternetGateways'), ('describe_vpcs', 'Vpcs')]: getattr(self.ec2, method).return_value = {key: []}
        self.assertTrue(cloud.cleanup_region(self.value, 'us-east-1')); self.assertTrue(cloud.cleanup_region(self.value, 'us-east-1'))
        self.ec2.describe_subnets.side_effect = AwsError('UnauthorizedOperation')
        with self.assertRaises(AwsError): cloud.cleanup_region(self.value, 'us-east-1')


class CatalogAndClients(unittest.TestCase):
    def test_aws_clients_have_bounded_timeouts_retries_and_sigv4_only_for_storage(self):
        import boto3
        for service, region in [('s3', 'us-west-2'), ('ec2', None)]:
            with self.subTest(service=service), patch.object(boto3, 'client', return_value='synthetic-client') as client:
                self.assertEqual(cloud.client(service, region), 'synthetic-client')
            args = client.call_args; self.assertEqual(args.args, (service,)); self.assertEqual(args.kwargs['region_name'], region)
            settings = args.kwargs['config']; self.assertEqual(settings.connect_timeout, 3); self.assertEqual(settings.read_timeout, 10); self.assertEqual(settings.retries, {'max_attempts': 2})
            self.assertEqual(settings.signature_version, 's3v4' if service == 's3' else None)

    def test_catalog_preserves_opt_in_status_and_explains_unavailable_zone_types(self):
        ec2 = MagicMock(); ec2.describe_regions.return_value = {'Regions': [{'RegionName': 'us-west-2', 'OptInStatus': 'opted-in'}, {'RegionName': 'ap-east-1'}, {'RegionName': 'us-east-1', 'OptInStatus': 'opt-in-not-required'}]}
        with patch.object(cloud, 'client', return_value=ec2):
            regions = cloud.regions(); self.assertEqual([row['region'] for row in regions], ['ap-east-1', 'us-east-1', 'us-west-2']); self.assertFalse(regions[0]['enabled']); self.assertEqual(regions[0]['opt_in_status'], 'unknown')
            with self.assertRaisesRegex(ValueError, 'must be enabled'): cloud.locations('ap-east-1')
            ec2.get_paginator.return_value.paginate.return_value = [{'InstanceTypeOfferings': [{'Location': 'use1-az1', 'InstanceType': 't3.medium'}, {'Location': 'use1-az2', 'InstanceType': 't3.large'}]}]
            ec2.describe_availability_zones.return_value = {'AvailabilityZones': [dict(ZoneId='use1-az1', ZoneName='us-east-1a', State='available', ZoneType='wavelength-zone'), dict(ZoneId='use1-az2', ZoneName='us-east-1b', State='impaired')]}
            zones = cloud.locations('us-east-1')
        self.assertFalse(zones[0]['available']); self.assertIn('carrier-gateway', zones[0]['reason']); self.assertEqual(zones[0]['instance_types'], ['t3.medium'])
        self.assertFalse(zones[1]['available']); self.assertIn('unavailable', zones[1]['reason'])


if __name__ == '__main__': unittest.main()
