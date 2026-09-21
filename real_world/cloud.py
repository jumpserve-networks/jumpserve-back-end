"""AWS discovery and tagged, per-test network/instance lifecycle."""
from config import AMI_PARAMETER, INSTANCE_TYPE, INSTANCE_TYPES

PROJECT = "JumpServeRealWorld"


def client(service, region=None):
    import boto3
    from botocore.config import Config
    return boto3.client(service, region_name=region, config=Config(connect_timeout=3, read_timeout=10,
        retries={"max_attempts": 2}, **({"signature_version": "s3v4"} if service == "s3" else {})))


def regions():
    return sorted([{"region": r["RegionName"], "enabled": r.get("OptInStatus") in ("opt-in-not-required", "opted-in"),
                    "opt_in_status": r.get("OptInStatus", "unknown")} for r in client("ec2").describe_regions(AllRegions=True)["Regions"]],
                  key=lambda r: r["region"])


def locations(region):
    if region not in {r["region"] for r in regions() if r["enabled"]}:
        raise ValueError("This Region must be enabled in the AWS account before it can be used.")
    ec2 = client("ec2", region)
    offerings = {}
    for page in ec2.get_paginator("describe_instance_type_offerings").paginate(
            LocationType="availability-zone-id", Filters=[{"Name": "instance-type", "Values": list(INSTANCE_TYPES)}]):
        for item in page["InstanceTypeOfferings"]:
            offerings.setdefault(item["Location"], []).append(item["InstanceType"])
    zones = []
    for zone in ec2.describe_availability_zones(AllAvailabilityZones=True)["AvailabilityZones"]:
        reason = None
        if zone.get("ZoneType") == "wavelength-zone":
            reason = "Wavelength requires carrier-gateway networking; this test uses public-IP networking."
        elif zone.get("OptInStatus") == "not-opted-in":
            reason = "Enable this zone in the AWS account first."
        elif zone["State"] != "available":
            reason = "AWS currently reports this zone as unavailable."
        elif not offerings.get(zone["ZoneId"]):
            reason = "t3.medium is not offered in this zone."
        zones.append({"zone_id": zone["ZoneId"], "name": zone["ZoneName"], "type": zone.get("ZoneType", "availability-zone"),
                      "available": reason is None, "reason": reason, "instance_types": sorted(offerings.get(zone["ZoneId"], []))})
    return sorted(zones, key=lambda z: z["name"])


def tags(job, name):
    return [{"Key": "Project", "Value": PROJECT}, {"Key": "JobId", "Value": job["job_id"]},
            {"Key": "Name", "Value": "jumpserve-real-" + name}, {"Key": "ExpiresAt", "Value": str(job["deadline"])}]


def specs(job, name, kind):
    return [{"ResourceType": kind, "Tags": tags(job, name)}]


def filters(job, name=None):
    result = [{"Name": "tag:Project", "Values": [PROJECT]}, {"Name": "tag:JobId", "Values": [job["job_id"]]}]
    if name:
        result.append({"Name": "tag:Name", "Values": ["jumpserve-real-" + name]})
    return result


BOOT = """#!/bin/bash
set -euo pipefail
# Shutdown is configured to terminate the instance, independently of the controller.
shutdown -h +60
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y --no-install-recommends wireguard-tools iperf3 iproute2 iptables ethtool python3
install -d -m 700 /var/lib/jumpserve
touch /var/lib/jumpserve/bootstrap-ready
"""


def provision(job, node, profile):
    if node.get("instance_type") != INSTANCE_TYPE:
        raise ValueError("Every machine must use t3.medium.")
    ec2 = client("ec2", node["region"])
    # Discover by tags on every retry: interrupted calls cannot orphan unrecorded resources.
    vpcs = ec2.describe_vpcs(Filters=filters(job))["Vpcs"]
    vpc = vpcs[0] if vpcs else ec2.create_vpc(CidrBlock="10.253.0.0/16", TagSpecifications=specs(job, "network", "vpc"))["Vpc"]
    vpc_id = vpc["VpcId"]
    if vpc.get("State") != "available":
        ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id], WaiterConfig={"Delay": 1, "MaxAttempts": 30})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    gateways = ec2.describe_internet_gateways(Filters=filters(job))["InternetGateways"]
    gateway = gateways[0] if gateways else ec2.create_internet_gateway(TagSpecifications=specs(job, "gateway", "internet-gateway"))["InternetGateway"]
    if not gateway.get("Attachments"):
        ec2.attach_internet_gateway(InternetGatewayId=gateway["InternetGatewayId"], VpcId=vpc_id)
    tables = ec2.describe_route_tables(Filters=filters(job))["RouteTables"]
    if not tables:
        tables = [ec2.create_route_table(VpcId=vpc_id, TagSpecifications=specs(job, "routes", "route-table"))["RouteTable"]]
    if not any(route.get("DestinationCidrBlock") == "0.0.0.0/0" for route in tables[0]["Routes"]):
        ec2.create_route(RouteTableId=tables[0]["RouteTableId"], DestinationCidrBlock="0.0.0.0/0", GatewayId=gateway["InternetGatewayId"])
    subnets = ec2.describe_subnets(Filters=filters(job, node["name"]))["Subnets"]
    index = next(i for i, item in enumerate(job["nodes"]) if item["name"] == node["name"])
    subnet = subnets[0] if subnets else ec2.create_subnet(VpcId=vpc_id, AvailabilityZoneId=node["zone_id"],
        CidrBlock=f"10.253.{index}.0/24", TagSpecifications=specs(job, node["name"], "subnet"))["Subnet"]
    tables = ec2.describe_route_tables(RouteTableIds=[tables[0]["RouteTableId"]])["RouteTables"]
    if not any(a.get("SubnetId") == subnet["SubnetId"] for a in tables[0].get("Associations", [])):
        ec2.associate_route_table(RouteTableId=tables[0]["RouteTableId"], SubnetId=subnet["SubnetId"])
    groups = ec2.describe_security_groups(Filters=filters(job, node["name"]))["SecurityGroups"]
    sg = groups[0]["GroupId"] if groups else ec2.create_security_group(VpcId=vpc_id,
        GroupName=f'{job["job_id"]}-{node["name"]}', Description="Ephemeral WireGuard peer; no public TCP ingress",
        TagSpecifications=specs(job, node["name"], "security-group"))["GroupId"]
    existing = [i for r in ec2.describe_instances(Filters=filters(job, node["name"]))["Reservations"] for i in r["Instances"]]
    if existing:
        instance = existing[0]
        if instance["State"]["Name"] in ("terminated", "shutting-down"):
            raise RuntimeError("A provisioned instance terminated before launch completed.")
        node.update(instance_id=instance["InstanceId"], image_id=instance["ImageId"], security_group=sg, state=instance["State"]["Name"])
        return
    image = node.get("image_id") or client("ssm", node["region"]).get_parameter(Name=AMI_PARAMETER)["Parameter"]["Value"]
    instance = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType=INSTANCE_TYPE,
        ClientToken=f'{job["job_id"]}-{node["name"]}', IamInstanceProfile={"Arn": profile},
        InstanceInitiatedShutdownBehavior="terminate", MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 1},
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 12, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
        NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnet["SubnetId"], "Groups": [sg], "AssociatePublicIpAddress": True, "DeleteOnTermination": True}],
        TagSpecifications=specs(job, node["name"], "instance") + specs(job, node["name"], "volume"), UserData=BOOT)["Instances"][0]
    node.update(instance_id=instance["InstanceId"], image_id=image, security_group=sg, state="pending")


def refresh(node):
    ec2 = client("ec2", node["region"])
    instance = ec2.describe_instances(InstanceIds=[node["instance_id"]])["Reservations"][0]["Instances"][0]
    node["state"] = instance["State"]["Name"]
    if node["state"] in ("terminated", "shutting-down", "stopped", "stopping"):
        raise RuntimeError(f'{node["name"]} unexpectedly entered {node["state"]}.')
    node["public_ip"] = instance.get("PublicIpAddress")
    return node["state"] == "running" and bool(node["public_ip"])


def allow_peers(job, node):
    ec2 = client("ec2", node["region"])
    peers = [item for item in job["nodes"] if item["role"] != "bottleneck"] if node["role"] == "bottleneck" else [
        item for item in job["nodes"] if item["role"] == "bottleneck"]
    for peer in peers:
        try:
            ec2.authorize_security_group_ingress(GroupId=node["security_group"], IpPermissions=[{
                "IpProtocol": "udp", "FromPort": 51820, "ToPort": 51820, "IpRanges": [{"CidrIp": peer["public_ip"] + "/32"}]}])
        except Exception as error:
            if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise


def cleanup_region(job, region):
    """Return true only after instances AND the per-test network have disappeared."""
    ec2 = client("ec2", region)
    reservations = ec2.describe_instances(Filters=filters(job))["Reservations"]
    active = [i for r in reservations for i in r["Instances"] if i["State"]["Name"] != "terminated"]
    if active:
        ec2.terminate_instances(InstanceIds=[i["InstanceId"] for i in active])
        return False
    for subnet in ec2.describe_subnets(Filters=filters(job))["Subnets"]:
        ec2.delete_subnet(SubnetId=subnet["SubnetId"])
    for group in ec2.describe_security_groups(Filters=filters(job))["SecurityGroups"]:
        ec2.delete_security_group(GroupId=group["GroupId"])
    for route_table in ec2.describe_route_tables(Filters=filters(job))["RouteTables"]:
        for association in route_table.get("Associations", []):
            if not association.get("Main"):
                ec2.disassociate_route_table(AssociationId=association["RouteTableAssociationId"])
        ec2.delete_route_table(RouteTableId=route_table["RouteTableId"])
    for gateway in ec2.describe_internet_gateways(Filters=filters(job))["InternetGateways"]:
        for attachment in gateway.get("Attachments", []):
            ec2.detach_internet_gateway(InternetGatewayId=gateway["InternetGatewayId"], VpcId=attachment["VpcId"])
        ec2.delete_internet_gateway(InternetGatewayId=gateway["InternetGatewayId"])
    for vpc in ec2.describe_vpcs(Filters=filters(job))["Vpcs"]:
        ec2.delete_vpc(VpcId=vpc["VpcId"])
    return True
