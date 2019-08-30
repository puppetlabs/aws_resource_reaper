import re
import os
import boto3

from reaper_class import ResourceReaper

# Terminator messages to be printed to the console, then sent to Slack
LIVE_TERMINATION_MESSAGE = (
    "REAPER TERMINATED {0} with ids {1} due to expired termination_date tags"
)
NOOP_TERMINATION_MESSAGE = (
    "REAPER NOOP: Would have terminated {0} with ids {1} due to expired termination_date tags"
)
STOPPED_MESSAGE = (
    "REAPER STOPPED {0} with ids {1} due to missing or unparsable termination_date tag"
)
NOOP_STOPPED = (
    "REAPER NOOP: Would have stopped {0} with ids {1} due to missing or unparsable termination_date tag"
)
IMPROPER_TAGS = "REAPER FOUND {0} with ids {1} are missing termination_date tags!"

# Determines whether or not LIVEMODE is true based on environment variable in AWS lambda
def determine_live_mode():
    """
    Returns True if LIVEMODE is set to true in the shell environment, False for
    all other cases.
    """
    if "LIVEMODE" in os.environ:
        return re.search(r"(?i)^true$", os.environ["LIVEMODE"]) is not None
    else:
        return False

# Whether or not the reaper should actually delete resources
LIVEMODE = True #determine_live_mode()

# This is the function that the schema_enforcer lambda should run when an instance hits
# the pending state.
def enforce(event, context):
    """
    :param event: AWS CloudWatch event; should be configured for when the state is pending.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    """
    ec2 = boto3.resource("ec2")
    print(event)
    print(event["detail"]["instance-id"])
    instance = ec2.Instance(id=event["detail"]["instance-id"])
    reaper = ResourceReaper(service=ec2, livemode=LIVEMODE)
    try:
        termination_date = reaper.wait_for_tags(instance)
        if termination_date:
            output = reaper.validate_ec2_termination_date(instance)
            print(output)
    except:
        # Here we should catch all exceptions, report on the state of the instance, and then
        # bubble up the original exception.
        instance.load()
        if instance.state["Name"] != "pending":
            print(
                "Instance {0} current state is {1}. This unexpected exception should be investigated!".format(
                    instance.id, instance.state["Name"]
                )
            )
        raise

    print("Schema successfully enforced.\n")

# Loops through services list to pass each to the reaper class
def resource_reaper(event, context):
    # TODO: consider breaking this function up into smaller functions, one per service
    """
    Loops through services listed in the SERVICES list and checks for
    termination dates for resources being used in those services. If
    termination_date tags are found and are expired, resources are
    destroyed. Resources without tags are reported to Slack

    : param: services: The list of services that should be checked
        example: [ec2 (elastic compute), elb (elastic load balancers)]

    Returns
        None
    """
    # List of AWS services to manage
    services = ["elb", "elbv2", "ec2"]

    for service in services:
        if service != "ec2":
            boto_resource = boto3.client(
                service,
                # Variables below for local testing
                # aws_access_key_id=access_key,
                # aws_secret_access_key=secret_key,
                # region_name=region,
            )
            reaper = ResourceReaper(service=boto_resource, livemode=LIVEMODE)
            items = reaper.terminate_expired_load_balancers()
            if items["deleted"]:
                if LIVEMODE:
                    print(
                        LIVE_TERMINATION_MESSAGE.format(service + "s", items["deleted"])
                    )
                else:
                    print(
                        NOOP_TERMINATION_MESSAGE.format(service + "s", items["deleted"])
                    )
            if items["improperly_tagged"]:
                print(IMPROPER_TAGS.format(service + "s", items["improperly_tagged"]))
            if "elbv2" in service:
                target_groups = reaper.terminate_expired_target_groups()
                if target_groups["deleted"]:
                    if LIVEMODE:
                        print(
                            LIVE_TERMINATION_MESSAGE.format(
                                "target_groups", target_groups["deleted"]
                            )
                        )
                    else:
                        print(
                            NOOP_TERMINATION_MESSAGE.format(
                                "target_groups", target_groups["deleted"]
                            )
                        )
                if target_groups["improperly_tagged"]:
                    print(
                        IMPROPER_TAGS.format(
                            "target_group", target_groups["improperly_tagged"]
                        )
                    )
        elif "ec2" in service:
            boto_resource = boto3.resource(
                service,
                # Variables below for local testing
                # aws_access_key_id=access_key,
                # aws_secret_access_key=secret_key,
                # region_name=region,
            )
            reaper = ResourceReaper(service=boto_resource, livemode=LIVEMODE)
            # EC2 resources to be deleted by the reaper
            resources = [
                "instances",
                "internet_gateways",
                "route_tables",
                "network_acls",
                "network_interfaces",
                "subnets",
                "security_groups",
                "vpcs",
                "volumes",
                "snapshots",
            ]
            # Loops through the resources list to delete resources attached to EC2 instances
            for resource in resources:
                items = reaper.terminate_expired_ec2_resources(resource)
                if items["deleted"]:
                    if LIVEMODE:
                        print(
                            LIVE_TERMINATION_MESSAGE.format(resource, items["deleted"])
                        )
                    else:
                        print(
                            NOOP_TERMINATION_MESSAGE.format(resource, items["deleted"])
                        )
                if items["improperly_tagged"]:
                    print(IMPROPER_TAGS.format(resource, items["improperly_tagged"]))
                if items["stopped"]:
                    if LIVEMODE:
                        print(STOPPED_MESSAGE.format(resource, items["stopped"]))
                    else:
                        print(NOOP_STOPPED.format(resource, items["stopped"]))
