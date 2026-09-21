"""Offline regression tests: no AWS account, credentials, or paid resources."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import artifacts
import cloud
import config
import controller
import runtime


def settings():
    placement = {"region": "us-east-1", "zone_id": "use1-az1", "instance_type": "t3.medium"}
    return {"server": dict(placement), "bottleneck": dict(placement), "receivers": [dict(placement), dict(placement)],
            "cca": "bbr", "duration_seconds": 30, "rate_mbit": 10, "buffer_kbytes": 125, "notes": "A hypothesis"}


def job(status="provisioning"):
    value = settings()
    return {"job_id": "job-1", "owner": "user-1", "config": value, "nodes": config.nodes_for(value),
            "status": status, "deadline": 2000000000, "active": "yes", "created_at": 1}


class AwsError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class ContractTests(unittest.TestCase):
    def test_complete_configuration(self):
        self.assertEqual(config.validate_config(settings()), settings())
        nodes = config.nodes_for(settings())
        self.assertEqual(len(nodes), 4)
        self.assertEqual(len({node["overlay_ip"] for node in nodes}), 4)
        self.assertEqual([n["port"] for n in nodes if n["role"] == "receiver"], [5201, 5202])

    def test_all_supported_sizes_are_accepted_independently_for_every_machine(self):
        for index in range(4):
            for instance_type in config.INSTANCE_TYPES:
                value = settings()
                [value["server"], value["bottleneck"], *value["receivers"]][index]["instance_type"] = instance_type
                with self.subTest(node=index, instance_type=instance_type):
                    self.assertEqual(config.validate_config(value), value)

    def test_reject_untrusted_and_unbounded_input(self):
        for key, value in [("cca", "bbr;shutdown"), ("cca", "bbr3"), ("duration_seconds", True),
                           ("duration_seconds", 601), ("rate_mbit", 0), ("rate_mbit", 1.5),
                           ("buffer_kbytes", 1), ("notes", "x" * 4001), ("receivers", []),
                           ("receivers", [settings()["server"]] * 17)]:
            with self.subTest(key=key, value=str(value)[:30]), self.assertRaises(ValueError):
                config.validate_config(dict(settings(), **{key: value}))
        for key, value in [("region", "us-east-1;echo"), ("zone_id", "us-east-1a"), ("instance_type", "p5.48xlarge")]:
            value_config = settings()
            value_config["server"][key] = value
            with self.assertRaises(ValueError):
                config.validate_config(value_config)

    def test_api_never_exposes_internal_commands_or_signed_urls(self):
        value = job()
        value.update(lease_until=500, upload_url="secret")
        value["nodes"][0].update(public_ip="1.2.3.4", prepare_command="secret-command")
        result = json.dumps(config.public_job(value))
        for forbidden in ["lease_until", "upload_url", "secret-command", "1.2.3.4"]:
            self.assertNotIn(forbidden, result)

    def test_public_job_includes_recorded_transfer_schedule_only_when_available(self):
        value = job()
        self.assertNotIn("start_epoch", config.public_job(value))
        value.update(status="starting", start_epoch=1900000000)
        public = config.public_job(value)
        self.assertEqual(public["start_epoch"], 1900000000)
        self.assertEqual(public["config"]["duration_seconds"], 30)


class CatalogTests(unittest.TestCase):
    def test_only_supported_t3_offerings_enable_zones(self):
        ec2 = MagicMock()
        ec2.get_paginator.return_value.paginate.return_value = [{"InstanceTypeOfferings": [
            {"Location": "use1-az1", "InstanceType": "t3.medium"},
            {"Location": "use1-az3", "InstanceType": "t3.small"},
            {"Location": "use1-az2", "InstanceType": "c6i.large"},
            {"Location": "use1-az4", "InstanceType": "t3.large"}]}]
        ec2.describe_availability_zones.return_value = {"AvailabilityZones": [
            {"ZoneId": f"use1-az{i}", "ZoneName": f"us-east-1{letter}", "State": "available",
             "OptInStatus": "not-opted-in" if i == 3 else "opt-in-not-required"}
            for i, letter in enumerate("abcd", 1)]}
        with patch.object(cloud, "regions", return_value=[{"region": "us-east-1", "enabled": True}]), \
                patch.object(cloud, "client", return_value=ec2):
            zones = cloud.locations("us-east-1")
        ec2.get_paginator.return_value.paginate.assert_called_once_with(
            LocationType="availability-zone-id", Filters=[{"Name": "instance-type", "Values": ["t3.small", "t3.medium", "t3.large"]}])
        self.assertEqual([z["available"] for z in zones], [True, False, False, True])
        self.assertEqual(zones[0]["instance_types"], ["t3.medium"])
        self.assertEqual(zones[3]["instance_types"], ["t3.large"])
        self.assertEqual(zones[1]["reason"], "None of t3.small, t3.medium, or t3.large is offered in this zone.")
        self.assertIn("Enable this zone", zones[2]["reason"])


class NetworkTests(unittest.TestCase):
    def test_artifact_upload_uses_the_same_content_type_as_the_signed_url(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(runtime.urllib.request, "urlopen", return_value=response) as upload:
            runtime.upload("https://example.test/report", {"success": True})
        request = upload.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.method, "PUT")
        value = job()
        value["start_epoch"] = 100
        value["nodes"][0]["instance_id"] = "i-test"
        with patch.object(cloud, "client") as aws, patch.object(artifacts, "signed_upload", return_value="https://example.test/report") as signed:
            aws.return_value.send_command.return_value = {"Command": {"CommandId": "command"}}
            controller.command(value, value["nodes"][0], "start")
        signed.assert_called_once_with("job-1", "server")

    def test_receivers_bind_overlay_and_server_is_sender(self):
        value = settings()
        value["node"] = config.nodes_for(value)[2]
        command = runtime.receiver_command(value)
        self.assertIn("-R", command)
        self.assertEqual(command[command.index("-c") + 1], "10.254.0.2")
        self.assertEqual(command[command.index("-B") + 1], "10.254.0.10")
        self.assertEqual(command[command.index("-C") + 1], "bbr")

    def test_one_shared_fifo_shaped_only_on_sender_direction(self):
        commands = runtime.qdisc_commands(settings())
        fifo = [c for c in commands if "bfifo" in c]
        self.assertEqual(len(fifo), 1)
        self.assertEqual(fifo[0][-1], "125000")
        self.assertEqual(len([c for c in commands if "filter" in c]), 1)
        self.assertIn("10.254.0.2/32", commands[-1])

    def test_spokes_have_only_hub_peer(self):
        nodes = config.nodes_for(settings())
        for i, node in enumerate(nodes):
            node.update(public_key=f"public{i}", public_ip=f"192.0.2.{i + 1}")
        with patch.object(Path, "read_text", return_value="private"):
            server = runtime.peer_config(nodes[0], nodes)
            receiver = runtime.peer_config(nodes[2], nodes)
            hub = runtime.peer_config(nodes[1], nodes)
        self.assertEqual(server.count("[Peer]"), 1)
        self.assertIn("Endpoint = 192.0.2.2:51820", server)
        self.assertIn("Endpoint = 192.0.2.2:51820", receiver)
        self.assertNotIn("Endpoint = 192.0.2.1", receiver)
        self.assertEqual(hub.count("[Peer]"), 3)
        self.assertIn("AllowedIPs = 10.254.0.10/32", hub)

    def test_security_group_only_accepts_wireguard_from_topology_peers(self):
        value = job()
        for i, node in enumerate(value["nodes"]):
            node.update(public_ip=f"192.0.2.{i + 1}", security_group=f"sg-{i}")
        ec2 = MagicMock()
        with patch.object(cloud, "client", return_value=ec2):
            cloud.allow_peers(value, value["nodes"][2])
        permissions = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"]
        self.assertEqual(permissions, [{"IpProtocol": "udp", "FromPort": 51820, "ToPort": 51820, "IpRanges": [{"CidrIp": "192.0.2.2/32"}]}])


class LifecycleTests(unittest.TestCase):
    def test_provisioning_retry_adopts_instance_and_enforces_ephemeral_launch(self):
        for index in range(4):
            for instance_type in config.INSTANCE_TYPES:
                with self.subTest(node=index, instance_type=instance_type):
                    value = job()
                    [value["config"]["server"], value["config"]["bottleneck"], *value["config"]["receivers"]][index]["instance_type"] = instance_type
                    value["nodes"] = config.nodes_for(value["config"])
                    node = value["nodes"][index]
                    ec2 = MagicMock()
                    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1", "State": "available"}]}
                    ec2.describe_internet_gateways.return_value = {"InternetGateways": [{"InternetGatewayId": "igw-1", "Attachments": [{"VpcId": "vpc-1"}]}]}
                    ec2.describe_route_tables.return_value = {"RouteTables": [{"RouteTableId": "rtb-1", "Routes": [{"DestinationCidrBlock": "0.0.0.0/0"}], "Associations": [{"SubnetId": "subnet-1"}]}]}
                    ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "subnet-1"}]}
                    ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupId": "sg-1"}]}
                    ec2.describe_instances.return_value = {"Reservations": []}
                    ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-1"}]}
                    ssm = MagicMock()
                    ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-1"}}
                    with patch.object(cloud, "client", side_effect=lambda service, region: ec2 if service == "ec2" else ssm):
                        cloud.provision(value, node, "profile")
                        ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"InstanceId": "i-1", "ImageId": "ami-1", "InstanceType": instance_type, "State": {"Name": "running"}}]}]}
                        cloud.provision(value, node, "profile")
                    ec2.run_instances.assert_called_once()
                    params = ec2.run_instances.call_args.kwargs
                    self.assertEqual(params["InstanceType"], instance_type)
                    self.assertEqual(params["ClientToken"], "job-1-" + node["name"])
                    self.assertEqual(params["InstanceInitiatedShutdownBehavior"], "terminate")
                    self.assertEqual(params["MetadataOptions"]["HttpTokens"], "required")
                    self.assertTrue(params["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
                    self.assertTrue(params["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
                    self.assertEqual(node["instance_id"], "i-1")
                    ec2.describe_instances.return_value["Reservations"][0]["Instances"][0]["InstanceType"] = "t3.xlarge"
                    with patch.object(cloud, "client", return_value=ec2), self.assertRaisesRegex(RuntimeError, "instance type differs"):
                        cloud.provision(value, node, "profile")
                    ec2.run_instances.assert_called_once()

    def test_provisioner_rejects_older_or_tampered_types_before_any_aws_call(self):
        for index in range(4):
            value = job()
            value["nodes"][index]["instance_type"] = "c6i.large"
            with self.subTest(node=index), patch.object(cloud, "client") as aws, \
                    self.assertRaisesRegex(ValueError, "must use t3.small, t3.medium, or t3.large"):
                cloud.provision(value, value["nodes"][index], "profile")
            aws.assert_not_called()

    def test_cancellation_cleans_up_before_allocating_anything_else(self):
        value = job()
        value["cancel_requested"] = True
        with patch.object(controller, "cleanup") as cleanup, patch.object(cloud, "provision") as provision:
            controller.step(value)
        cleanup.assert_called_once()
        provision.assert_not_called()
        self.assertEqual(value["outcome"], "cancelled")

    def test_deadline_independently_forces_cleanup(self):
        value = job("running")
        value["deadline"] = 1
        with patch.object(controller, "cleanup") as cleanup:
            controller.step(value)
        self.assertEqual(value["outcome"], "failed")
        cleanup.assert_called_once()

    def test_partial_failure_still_cleans_every_region(self):
        value = job("cleaning")
        value["nodes"][-1]["region"] = "us-west-2"
        with patch.object(cloud, "cleanup_region", side_effect=[RuntimeError("denied"), True]) as cleanup:
            controller.cleanup(value)
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(value["status"], "cleaning")
        self.assertIn("denied", value["cleanup_error"])

    def test_terminal_status_requires_all_resource_deletions(self):
        value = job("cleaning")
        value["outcome"] = "completed"
        with patch.object(cloud, "cleanup_region", return_value=False):
            controller.cleanup(value)
        self.assertEqual(value["status"], "cleaning")
        with patch.object(cloud, "cleanup_region", return_value=True):
            controller.cleanup(value)
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["active"], "no")

    def test_cloud_cleanup_waits_for_termination_before_deleting_network(self):
        ec2 = MagicMock()
        ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": "running"}}]}]}
        with patch.object(cloud, "client", return_value=ec2):
            self.assertFalse(cloud.cleanup_region(job(), "us-east-1"))
        ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-1"])
        ec2.delete_vpc.assert_not_called()
        requested_filters = ec2.describe_instances.call_args.kwargs["Filters"]
        self.assertIn({"Name": "tag:JobId", "Values": ["job-1"]}, requested_filters)

    def test_worker_failure_persists_cleanup_and_releases_lease(self):
        value = job()
        with patch.object(controller.store, "claim", return_value=True), patch.object(controller.store, "load", return_value=value), \
             patch.object(controller.store, "save") as save, patch.object(controller.store, "release") as release, \
             patch.object(controller, "step", side_effect=RuntimeError("capacity exhausted")):
            self.assertFalse(controller.tick("job-1")["finished"])
        self.assertEqual(save.call_args.args[0]["status"], "cleaning")
        release.assert_called_once_with("job-1", True)

    def test_duplicate_worker_does_not_touch_resources(self):
        with patch.object(controller.store, "claim", return_value=False), patch.object(controller, "step") as step:
            self.assertFalse(controller.tick("job-1")["finished"])
        step.assert_not_called()

    def test_missing_artifacts_are_pending_not_zero_measurements(self):
        with patch.object(artifacts, "records", return_value=[]), patch.object(artifacts, "read", return_value=None):
            value = job("running")
            self.assertFalse(controller.collect(value))
        self.assertNotIn("results", value)

    def test_collect_preserves_units_and_only_then_begins_cleanup(self):
        value = job("running")
        report = {"success": True, "started_at": 100, "iperf": {"end": {"sum_received": {"bits_per_second": 12_500_000, "bytes": 1000, "seconds": 30}}}}
        with patch.object(artifacts, "records", return_value=[]), patch.object(artifacts, "read", return_value=(json.dumps(report).encode(), {})):
            self.assertTrue(controller.collect(value))
        self.assertEqual(len(value["results"]), 2)
        self.assertEqual(value["results"][0]["received_mbit_per_second"], 12.5)
        self.assertEqual(value["status"], "cleaning")


class ApiTests(unittest.TestCase):
    def test_launch_rejects_other_types_for_every_machine_before_storage_or_aws(self):
        for index in range(4):
            for instance_type in ["c7i.large", "c6i.large", "c5.large", "m7i.large", "m6i.large", "m5.large", "t3.micro", "t3.xlarge"]:
                value = settings()
                [value["server"], value["bottleneck"], *value["receivers"]][index]["instance_type"] = instance_type
                event = {"rawPath": "/real-world/tests", "requestContext": {"http": {"method": "POST"}},
                         "body": json.dumps({"config": value, "request_id": "a1000000-0000-4000-a000-000000000001"})}
                with self.subTest(node=index, instance_type=instance_type), patch.object(api, "authenticate", return_value="user-1"), \
                        patch.object(api.store, "load") as load, patch.object(cloud, "client") as aws:
                    response = api.handler(event, None)
                self.assertEqual(response["statusCode"], 400)
                self.assertIn("Every machine must use t3.small, t3.medium, or t3.large.", response["body"])
                load.assert_not_called()
                aws.assert_not_called()

    def test_unauthenticated_call_does_not_access_aws(self):
        with patch.object(api, "dispatch") as dispatch:
            response = api.handler({"headers": {}}, None)
        self.assertEqual(response["statusCode"], 401)
        dispatch.assert_not_called()

    def test_forged_sessions_are_denied_before_privileged_access(self):
        import urllib.error
        event = {"headers": {"Authorization": "Bearer forged"}, "rawPath": "/real-world/tests",
                 "requestContext": {"http": {"method": "POST"}}}
        error = urllib.error.HTTPError("https://auth.example", 401, "Unauthorized", {}, None)
        with patch.dict(os.environ, SUPABASE_URL="https://auth.example", SUPABASE_ANON_KEY="public-key"), \
                patch.object(api.urllib.request, "urlopen", side_effect=error), patch.object(api, "dispatch") as dispatch:
            response = api.handler(event, None)
        self.assertEqual(response["statusCode"], 401)
        dispatch.assert_not_called()

    def test_other_users_can_read_but_cannot_cancel(self):
        value = job()
        value["schema_version"] = config.SCHEMA_VERSION
        with patch.object(api.store, "load", return_value=value):
            event = {"rawPath": "/real-world/tests/job-1", "requestContext": {"http": {"method": "GET"}}}
            response = api.handler(event, None)
            self.assertEqual(response["statusCode"], 200)
            self.assertFalse(json.loads(response["body"])["can_manage"])
            self.assertNotIn("owner", json.loads(response["body"]))
            self.assertTrue(api.dispatch(event, "user-1")["can_manage"])
            event["rawPath"] += "/cancel"
            event["requestContext"]["http"]["method"] = "POST"
            with self.assertRaises(api.HttpError) as error:
                api.dispatch(event, "other-user")
            self.assertEqual(error.exception.status, 404)

    def test_anonymous_mutations_are_denied_before_storage(self):
        for path in ["/real-world/tests", "/real-world/tests/job-1/cancel"]:
            with patch.object(api.store, "load") as load, patch.object(api.cloud, "client") as aws:
                response = api.handler({"rawPath": path, "requestContext": {"http": {"method": "POST"}}}, None)
                self.assertEqual(response["statusCode"], 401)
                load.assert_not_called()
                aws.assert_not_called()

    def test_retry_reuses_workflow_name_and_never_creates_second_job(self):
        value = job()
        body = {"config": value["config"], "request_id": "a1000000-0000-4000-a000-000000000001"}
        with patch.object(api.store, "load", return_value=value), patch.object(api.store, "create") as table, \
             patch.object(cloud, "client") as aws, patch.dict(os.environ, STATE_MACHINE_ARN="machine"):
            api.launch(body, "user-1")
            api.launch(body, "user-1")
        table.assert_not_called()
        starts = aws.return_value.start_execution.call_args_list
        self.assertEqual(starts[0].kwargs["name"], starts[1].kwargs["name"])

    def test_changed_request_with_same_id_is_rejected(self):
        value = job()
        changed = copy.deepcopy(value["config"])
        changed["cca"] = "reno"
        with patch.object(api.store, "load", return_value=value), self.assertRaises(api.HttpError) as error:
            api.launch({"config": changed, "request_id": "a1000000-0000-4000-a000-000000000001"}, "user-1")
        self.assertEqual(error.exception.status, 409)

    def test_launch_preserves_mixed_supported_sizes_and_verifies_exact_offerings(self):
        value = settings()
        value["server"]["instance_type"] = "t3.small"
        value["bottleneck"]["instance_type"] = "t3.large"
        value["receivers"][1]["instance_type"] = "t3.small"
        body = {"config": value, "request_id": "a1000000-0000-4000-a000-000000000001"}
        zone = {"zone_id": "use1-az1", "available": True, "instance_types": list(config.INSTANCE_TYPES)}
        with patch.object(api.store, "load", return_value=None), patch.object(api.store, "create", return_value=True) as create, \
                patch.object(cloud, "locations", return_value=[zone]), patch.object(cloud, "client") as aws, \
                patch.dict(os.environ, STATE_MACHINE_ARN="machine", RUNTIME_REVISION="test-runtime"):
            result = api.launch(body, "user-1")
        self.assertEqual(result["config"], value)
        self.assertEqual([node["instance_type"] for node in create.call_args.args[0]["nodes"]],
                         ["t3.small", "t3.large", "t3.medium", "t3.small"])
        aws.return_value.start_execution.assert_called_once()
        zone["instance_types"] = ["t3.medium"]
        with patch.object(api.store, "load", return_value=None), patch.object(api.store, "create") as create, \
                patch.object(cloud, "locations", return_value=[zone]), self.assertRaisesRegex(ValueError, "server:.*unavailable"):
            api.launch(body, "user-1")
        create.assert_not_called()

    def test_live_availability_is_checked_before_persisting(self):
        body = {"config": settings(), "request_id": "a1000000-0000-4000-a000-000000000001"}
        with patch.object(api.store, "load", return_value=None), patch.object(api.store, "create") as table, \
             patch.object(cloud, "locations", return_value=[]), self.assertRaises(ValueError):
            api.launch(body, "user-1")
        table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
