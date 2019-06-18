from __future__ import print_function

import datetime
import time
import dateutil
import re
import os
from warnings import warn
import boto3

ec2 = boto3.resource('ec2')
elb = boto3.client('elb')
elbv2 = boto3.client('elbv2')

def determine_live_mode():
    """
    Returns True if LIVEMODE is set to true in the shell environment, False for
    all other cases.
    """
    if 'LIVEMODE' in os.environ:
        return re.search(r'(?i)^true$', os.environ['LIVEMODE']) is not None
    else:
        return False

# The `LIVEMODE` environment variable controls if this script is actually
# running and reaping in your AWS environment. To turn reaping on, set
# the `LIVEMODE` environment variable to true in your Lambda environment.
LIVEMODE = determine_live_mode()

# The `MINUTES_TO_WAIT` global variable is the number of minutes to wait for
# a termination_date tag to appear for the EC2 instance. Please note that the
# AWS Lambdas are limited to a 5 minute maximum for their total run time.
MINUTES_TO_WAIT = 4

#The Indefinite lifetime constant
INDEFINITE = 'indefinite'

def get_tag(tag_array, tag_name):
    """
    :param tag_array: an array of tags with Key/Value pairs.
    :param tag_name: a string of the key name you are searching for.

    This method returns None if the ec2 instance currently has no tags
    or if the tag is not found. If the tag is found, it returns the tag
    value.
    """
    if tag_array is None:
        return None
    for tag in tag_array:
        if tag['Key'] == tag_name:
            return tag['Value']
    return None

def timenow_with_utc():
    """
    Return a datetime object that includes the tzinfo for utc time.
    """
    time = datetime.datetime.utcnow()
    time = time.replace(tzinfo=dateutil.tz.tz.tzutc())
    return time

def wait_for_tags(ec2_instance, wait_time):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance
    :param wait_time: The number of minutes to wait for the 'termination_date'

    This method returns when a 'termination_date' is found and raises an exception
    and terminates the instance when the wait_time has passed. The method looks
    for the 'lifetime' key, parses it, and sets the 'termination_date' on the
    instance. The 'termination_date' can be set directly on the instance, bypassing
    the steps to parse the lifetime key and allowing this to return.

    This returns the termination_date value if successful; otherwise, it returns
    None.
    """
    start = timenow_with_utc()
    timeout = start + datetime.timedelta(minutes=wait_time)

    while timenow_with_utc() < timeout:
        ec2_instance.load()
        termination_date = get_tag(ec2_instance.tags, 'termination_date')
        if termination_date:
            print("'termination_date' tag found!")
            return termination_date
        instance_name = get_tag(ec2_instance.tags, 'Name')
        try:
            if 'opsworks' in instance_name:
                ec2_instance.create_tags(
                    Tags=[
                        {
                            'Key': 'termination_date',
                            'Value': INDEFINITE
                        }
                    ]
                )
                return
        except:
            print("No 'Name' tag specified")
        lifetime = get_tag(ec2_instance.tags, 'lifetime')
        if not lifetime:
            print("No 'lifetime' tag found; sleeping for 15s")
            time.sleep(15)
            continue
        print('lifetime tag found')
        if lifetime == INDEFINITE:
            ec2_instance.create_tags(
                Tags=[
                    {
                        'Key': 'termination_date',
                        'Value': INDEFINITE
                    }
                ]
            )
            return
        lifetime_match = validate_lifetime_value(lifetime)
        if not lifetime_match:
            terminate_instance(ec2_instance, 'Invalid lifetime value supplied')
            return
        lifetime_delta = calculate_lifetime_delta(lifetime_match)
        future_termination_date = start + lifetime_delta
        ec2_instance.create_tags(
            Tags=[
                {
                    'Key': 'termination_date',
                    'Value': future_termination_date.isoformat()
                }
            ]
        )

    # If the above while condition does not return after finding a termination_date,
    # terminate the instance and raise an exception.
    terminate_instance(ec2_instance,
                       'No termination_date found within {0} minutes of creation'.format(wait_time))

def delete_vpc(vpc_id, message):
    output = "REAPER TERMINATION: {1} for VPC name={0}\n".format(vpc_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting VPC {0}'.format(vpc_id)
        print(output)
        vpc = ec2.Vpc(vpc_id)
        vpc.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted VPC {1}".format(LIVEMODE, vpc_id)
        print(output)

def delete_security_group(sg_id, message):
    output = "REAPER TERMINATION: {1} for security group name={0}\n".format(sg_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting security group {0}'.format(sg_id)
        print(output)
        security_group = ec2.SecurityGroup(sg_id)
        security_group.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted security group {1}".format(LIVEMODE, sg_id)
        print(output)

def delete_internet_gateway(ig_id, message):
    output = "REAPER TERMINATION: {1} for internet gateway name={0}\n".format(ig_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting internet gateway {0}'.format(ig_id)
        print(output)
        internet_gateway = ec2.InternetGateway(ig_id)
        internet_gateway.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted internet gateway {1}".format(LIVEMODE, ig_id)
        print(output)

def delete_route_table(rt_id, message):
    output = "REAPER TERMINATION: {1} for route table name={0}\n".format(rt_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting route table {0}'.format(rt_id)
        print(output)
        route_table = ec2.RouteTable(rt_id)

        # Delete the route table/subnet associations first
        route_table_associations = route_table.associations_attribute
        for route_table_association in route_table_associations:
            # Cannot delete the main route table association
            if not route_table_association.main:
                rta_id = route_table_association['RouteTableAssociationId']
                rta = ec2.RouteTableAssociation(rta_id)
                rta.delete()

        route_table.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted route table {1}".format(LIVEMODE, rt_id)
        print(output)

def delete_network_acl(na_id, message):
    output = "REAPER TERMINATION: {1} for network ACL name={0}\n".format(na_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting network ACL {0}'.format(na_id)
        print(output)
        network_acl = ec2.NetworkAcl(na_id)
        if not network_acl.is_default:
            network_acl.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted network ACL {1}".format(LIVEMODE, na_id)
        print(output)

def delete_network_interface(ni_id, message):
    output = "REAPER TERMINATION: {1} for network interface name={0}\n".format(ni_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting network interface {0}'.format(ni_id)
        print(output)
        network_interface = ec2.NetworkInterface(ni_id)
        network_interface.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted network interface {1}".format(LIVEMODE, ni_id)
        print(output)

def delete_subnet(sn_id, message):
    output = "REAPER TERMINATION: {1} for subnet name={0}\n".format(sn_id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting subnet {0}'.format(sn_id)
        print(output)
        subnet = ec2.Subnet(sn_id)
        subnet.delete()
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted subnet {1}".format(LIVEMODE, sn_id)
        print(output)

def delete_target_group(tg_arn, message):
    output = "REAPER TERMINATION: {1} for target group name={0}\n".format(tg_arn, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting target group {0}'.format(tg_arn)
        print(output)
        elbv2.delete_target_group(TargetGroupArn=tg_arn)
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted target group {1}".format(LIVEMODE, tg_arn)
        print(output)

def delete_load_balancer(lb_name, message):
    """
    :param lb_name: a classic load balancer name
    :param message: string explaining why the load balancer is being deleted.

    Prints a message and terminates a load balancer if LIVEMODE is True.
    Otherwise, print out the name of the load balancer that would have been
    deleted.
    """

    output = "REAPER TERMINATION: {1} for load balancer name={0}\n".format(lb_name, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting load balancer {0}'.format(lb_name)
        print(output)
        elb.delete_load_balancer(LoadBalancerName=lb_name)
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted load balancer {1}".format(LIVEMODE, lb_name)
        print(output)

def delete_v2_load_balancer(lb_arn, message):
    """
    :param lb_arn: an Amazon Resource Name for a load balancer.
    :param message: string explaining why the load balancer is being deleted.

    Prints a message and terminates a load balancer if LIVEMODE is True.
    Otherwise, print out the ARN of the load balancer that would have been
    deleted.
    """

    output = "REAPER TERMINATION: {1} for load balancer ARN={0}\n".format(lb_arn, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting load balancer {0}'.format(lb_arn)
        print(output)
        elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)

        # Wait for the load balancer to be deleted
        waiter = elbv2.get_waiter('load_balancers_deleted')
        waiter.wait(LoadBalancerArns=[lb_arn])
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted load balancer {1}".format(LIVEMODE, lb_arn)
        print(output)

def terminate_instance(ec2_instance, message):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
    :param message: string explaining why the instance is being terminated.

    Prints a message and terminates an instance if LIVEMODE is True. Otherwise, print out
    the instance id of EC2 resource that would have been deleted.
    """
    output = "REAPER TERMINATION: {1} for ec2_instance_id={0}\n".format(ec2_instance.id, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting instance {0}'.format(ec2_instance.id)
        print(output)
        ec2_instance.terminate()
        waiter = ec2.meta.client.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=[ec2_instance.id])
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted instance {1}".format(LIVEMODE, ec2_instance.id)
        print(output)

def stop_instance(ec2_instance, message):
    """

    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
    :param message: string explaining why the instance is being terminated

    Prints a message and stop an instance if LIVEMODE is True. Otherwise, print out
    the instance id of the EC2 resource that would have been deleted
    """
    output = "REAPER STOP message (ec2_instance_id{0}): {1}\n".format(ec2_instance.id, message)
    if LIVEMODE:
        output += 'REAPER STOP enabled: stopping instance {0}'.format(ec2_instance.id)
        print(output)
        ec2_instance.stop()
    else:
        output += "REAPER STOP not enabled: LIVEMODE is {0}. Would have stopped instance {1}".format(LIVEMODE, ec2_instance.id)
        print(output)

def validate_ec2_termination_date(ec2_instance):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.

    Validates that an ec2 instance has a valid termination_date in the future.
    Otherwise, delete the instance.
    """
    termination_date = get_tag(ec2_instance.tags, 'termination_date')
    try:
        dateutil.parser.parse(termination_date) - timenow_with_utc()
    except Exception as e:
        if e is TypeError:
            if re.search(r'(offset-naive).+(offset-aware)', e.__str__):
                terminate_instance(ec2_instance,
                                   'The termination_date requires a UTC offset')
            else:
                terminate_instance(ec2_instance,
                                   'Unable to parse the termination_date')
            return

    if dateutil.parser.parse(termination_date) > timenow_with_utc():
        ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
        print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
    else:
        terminate_instance(ec2_instance,
                           'The termination_date has passed')

def validate_lifetime_value(lifetime_value):
    """
    :param lifetime_value: A string from your ec2 instance.

    Return a match object if a match is found; otherwise, return the None from the search method.
    """
    search_result = re.search(r'^([0-9]+)(w|d|h|m)$', lifetime_value)
    if search_result is None:
        return None
    toople = search_result.groups()
    unit = toople[1]
    length = int(toople[0])
    return (length, unit)

def calculate_lifetime_delta(lifetime_tuple):
    """
    :param lifetime_match: Resulting regex match object from validate_lifetime_value.

    Check the value of the lifetime. If not indefinite convert the regex match from
    `validate_lifetime_value` into a datetime.timedelta.
    """
    length = lifetime_tuple[0]
    unit = lifetime_tuple[1]
    if unit == 'w':
        return datetime.timedelta(weeks=length)
    elif unit == 'h':
        return datetime.timedelta(hours=length)
    elif unit == 'd':
        return datetime.timedelta(days=length)
    elif unit == 'm':
        return datetime.timedelta(minutes=length)
    else:
        raise ValueError("Unable to parse the unit '{0}'".format(unit))


# This is the function that the schema_enforcer lambda should run when an instance hits
# the pending state.
def enforce(event, context):
    """
    :param event: AWS CloudWatch event; should be a configured for when the state is pending.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    """
    print(event)
    print(event['detail']['instance-id'])
    instance = ec2.Instance(id=event['detail']['instance-id'])
    try:
        termination_date = wait_for_tags(instance, MINUTES_TO_WAIT)
        if termination_date == INDEFINITE:
            return
        elif termination_date:
            validate_ec2_termination_date(instance)
    except Exception as e:
        # Here we should catch all exceptions, report on the state of the instance, and then
        # bubble up the original exception.
        instance.load()
        warn('Instance {0} current state is {1}. This unexpected exception should be investigated!'.format(instance.id, instance.state['Name']))
        #  TODO: add in code to alert somebody exception happened, or remove
        # this comment if cloudwatch starts watching for exceptions from
        # this lambda
        raise

    print('Schema successfully enforced.')

def terminate_expired_vpcs():
    improperly_tagged = []
    deleted_vpcs = []

    # Get the VPCs that will be deleted after everything else
    vpcs = ec2.vpcs.all()
    for vpc in vpcs:
        vpc_id = vpc.id
        termination_date = get_tag(vpc.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for VPC {0}".format(vpc_id))
            improperly_tagged.append(vpc_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("VPC {0} will be deleted {1} seconds from now, roughly".format(vpc_id, ttl.seconds))
                else:
                    delete_vpc(vpc_id, "VPC {0} has expired".format(vpc_id))
                    deleted_vpcs.append(vpc_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for VPC {0}".format(vpc_id, termination_date))
                continue
        else:
            continue

def terminate_expired_internet_gateways():
    improperly_tagged = []
    deleted_internet_gateways = []

    internet_gateways = ec2.internet_gateways.all()
    for internet_gateway in internet_gateways:
        ig_id = internet_gateway.id
        termination_date = get_tag(internet_gateway.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for internet gateway {0}".format(ig_id))
            improperly_tagged.append(ig_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Internet gateway {0} will be deleted {1} seconds from now, roughly".format(ig_id, ttl.seconds))
                else:
                    delete_internet_gateway(ig_id, "Internet gateway {0} has expired".format(ig_id))
                    deleted_internet_gateways.append(ig_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for internet gateway {0}".format(ig_id, termination_date))
                continue
        else:
            continue

def terminate_expired_route_tables():
    improperly_tagged = []
    deleted_route_tables = []

    route_tables = ec2.route_tables.all()
    for route_table in route_tables:
        rt_id = route_table.id
        termination_date = get_tag(route_table.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for route table {0}".format(rt_id))
            improperly_tagged.append(rt_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Route table {0} will be deleted {1} seconds from now, roughly".format(rt_id, ttl.seconds))
                else:
                    delete_route_table(rt_id, "Route table {0} has expired".format(rt_id))
                    deleted_route_tables.append(rt_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for route table {0}".format(rt_id, termination_date))
                continue
        else:
            continue

def terminate_expired_network_acls():
    improperly_tagged = []
    deleted_network_acls = []

    network_acls = ec2.network_acls.all()
    for network_acl in network_acls:
        na_id = network_acl.id
        termination_date = get_tag(network_acl.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for network ACL {0}".format(na_id))
            improperly_tagged.append(na_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Network ACL {0} will be deleted {1} seconds from now, roughly".format(na_id, ttl.seconds))
                else:
                    delete_network_acl(na_id, "Network ACL {0} has expired".format(na_id))
                    deleted_network_acls.append(na_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for network ACL {0}".format(na_id, termination_date))
                continue
        else:
            continue

def terminate_expired_network_interfaces():
    improperly_tagged = []
    deleted_network_interfaces = []

    network_interfaces = ec2.network_interfaces.all()
    for network_interface in network_interfaces:
        ni_id = network_interface.id
        termination_date = get_tag(network_interface.tag_set, 'termination_date')

        if termination_date is None:
            print("No termination date found for network interface {0}".format(ni_id))
            improperly_tagged.append(ni_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Network interface {0} will be deleted {1} seconds from now, roughly".format(ni_id, ttl.seconds))
                else:
                    delete_network_interface(ni_id, "Network interface {0} has expired".format(ni_id))
                    deleted_network_interfaces.append(ni_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for network interface {0}".format(ni_id, termination_date))
                continue
        else:
            continue

def terminate_expired_security_groups():
    improperly_tagged = []
    deleted_security_groups = []

    security_groups = ec2.security_groups.all()
    for security_group in security_groups:
        sg_id = security_group.id
        termination_date = get_tag(security_group.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for security group {0}".format(sg_id))
            improperly_tagged.append(sg_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Security group {0} will be deleted {1} seconds from now, roughly".format(sg_id, ttl.seconds))
                else:
                    delete_security_group(sg_id, "Security group {0} has expired".format(sg_id))
                    deleted_security_groups.append(sg_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for security group {0}".format(sg_id, termination_date))
                continue
        else:
            continue

def terminate_expired_subnets():
    improperly_tagged = []
    deleted_subnets = []

    # Get the subnets that will be deleted after the instances
    subnets = ec2.subnets.all()
    for subnet in subnets:
        sn_id = subnet.id
        termination_date = get_tag(subnet.tags, 'termination_date')

        if termination_date is None:
            print("No termination date found for subnet {0}".format(sn_id))
            improperly_tagged.append(sn_id)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Subnet {0} will be deleted {1} seconds from now, roughly".format(sn_id, ttl.seconds))
                else:
                    delete_subnet(sn_id, "Subnet {0} has expired".format(sn_id))
                    deleted_subnets.append(sn_id)
            except Exception as e:
                print("Unable to parse the termination_date {1} for subnet {0}".format(sn_id, termination_date))
                continue
        else:
            continue

def terminate_expired_target_groups():
    improperly_tagged = []
    deleted_target_groups = []

    # Get the target groups that will be deleted after the load balancer
    target_groups = elbv2.describe_target_groups()
    target_groups_array = target_groups['TargetGroups']
    for target_group in target_groups_array:
        tg_arn = target_group['TargetGroupArn']
        tags = elbv2.describe_tags(ResourceArns=[tg_arn])
        tag_descriptions = tags['TagDescriptions']
        tag_array = tag_descriptions[0]['Tags']
        termination_date = get_tag(tag_array, 'termination_date')

        if termination_date is None:
            print("No termination date found for target group {0}".format(tg_arn))
            improperly_tagged.append(tg_arn)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Target group {0} will be deleted {1} seconds from now, roughly".format(tg_arn, ttl.seconds))
                else:
                    delete_target_group(tg_arn, "Target group {0} has expired".format(tg_arn))
                    deleted_target_groups.append(tg_arn)
            except Exception as e:
                print("Unable to parse the termination_date {1} for target group {0}".format(tg_arn, termination_date))
                continue
        else:
            continue

def terminate_expired_classic_load_balancers():
    improperly_tagged = []
    deleted_load_balancers = []

    load_balancers = elb.describe_load_balancers()

    for load_balancer in load_balancers['LoadBalancerDescriptions']:
        lb_name = load_balancer['LoadBalancerName']
        tags = elb.describe_tags(LoadBalancerNames=[lb_name])
        tag_descriptions = tags['TagDescriptions']
        tag_array = tag_descriptions[0]['Tags']
        termination_date = get_tag(tag_array, 'termination_date')

        if termination_date is None:
            print("No termination date found for load balancer {0}".format(lb_name))
            improperly_tagged.append(lb_name)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Load balancer {0} will be deleted {1} seconds from now, roughly".format(lb_name, ttl.seconds))
                else:
                    delete_load_balancer(lb_name, "Load balancer {0} has expired".format(lb_name))
                    deleted_load_balancers.append(lb_name)
            except Exception as e:
                print("Unable to parse the termination_date {1} for load balancer {0}".format(lb_name, termination_date))
                continue
        else:
            continue

def terminate_expired_v2_load_balancers():
    improperly_tagged = []
    deleted_load_balancers = []

    load_balancers = elbv2.describe_load_balancers()

    for load_balancer in load_balancers['LoadBalancers']:
        lb_arn = load_balancer['LoadBalancerArn']
        tags = elbv2.describe_tags(ResourceArns=[lb_arn])
        tag_descriptions = tags['TagDescriptions']
        tag_array = tag_descriptions[0]['Tags']
        termination_date = get_tag(tag_array, 'termination_date')

        if termination_date is None:
            print("No termination date found for load balancer {0}".format(lb_arn))
            improperly_tagged.append(lb_arn)
            continue

        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("Load balancer {0} will be deleted {1} seconds from now, roughly".format(lb_arn, ttl.seconds))
                else:
                    delete_v2_load_balancer(lb_arn, "Load balancer {0} has expired".format(lb_arn))
                    deleted_load_balancers.append(lb_arn)
            except Exception as e:
                print("Unable to parse the termination_date {1} for load balancer {0}".format(lb_arn, termination_date))
                continue
        else:
            continue

def terminate_expired_instances():
    improperly_tagged = []
    deleted_instances = []

    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    for instance in instances:
        termination_date = get_tag(instance.tags, 'termination_date')
        if termination_date is None:
            print("No termination date found for {0}".format(instance.id))
            stop_instance(instance, "EC2 instance has no termination_date")
            improperly_tagged.append(instance)
            continue
        if termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(termination_date) - timenow_with_utc()
                    print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
                else:
                    terminate_instance(instance, "EC2 instance has expired")
                    deleted_instances.append(instance)
            except Exception as e:
                print("Unable to parse the termination_date for {0}".format(instance.id))
                stop_instance(instance, "EC2 instance has invalid termination_date")
                improperly_tagged.append(instance)
                continue
        if termination_date == INDEFINITE:
            continue

    if LIVEMODE:
        if len(improperly_tagged) > 0 and len(deleted_instances) < 1:
            print(("REAPER TERMINATION completed. The following instances have been stopped due to unparsable or missing termination_date tags: {0}.").format(improperly_tagged))
        elif len(deleted_instances) > 0 and len(improperly_tagged) < 1:
            print(("REAPER TERMINATION completed. The following instances have been deleted due to expired termination_date tags: {0}.").format(deleted_instances))
        else:
            print(("REAPER TERMINATION completed. The following instances have been deleted due to expired termination_date tags: {0}. "
                   "The following instances have been stopped due to unparsable or missing termination_date tags: {1}.").format(deleted_instances, improperly_tagged))
    else:
        if len(improperly_tagged) > 0 and len(deleted_instances) < 1:
            print("REAPER TERMINATION completed. LIVEMODE is off, would have stopped the following instances due to unparsable or missing termination_date tags: {0} ".format(improperly_tagged))
        elif len(deleted_instances) > 0 and len(improperly_tagged) < 1:
            print("REAPER TERMINATION completed. LIVEMODE is off, would have deleted the following instances: {0}. ".format(deleted_instances))
        else:
            print(("REAPER TERMINATION completed. LIVEMODE is off, would have deleted the following instances: {0}. "
                   "REAPER would have stopped the following instances due to unparsable or missing termination_date tags: {1}").format(deleted_instances, improperly_tagged))

# This is the function that a terminator lambda should call periodically to
# delete instances past their termination_date.
def terminate_expired_resources(event, context):
    """
    :param event: AWS CloudWatch event; should be a Cloudwatch Scheduled Event.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    """
    terminate_expired_instances()
    terminate_expired_classic_load_balancers()
    terminate_expired_v2_load_balancers()
    terminate_expired_target_groups()
    terminate_expired_internet_gateways()
    terminate_expired_route_tables()
    terminate_expired_network_acls()
    terminate_expired_network_interfaces()
    terminate_expired_subnets()
    terminate_expired_security_groups()
    terminate_expired_vpcs()
