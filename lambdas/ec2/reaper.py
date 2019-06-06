from __future__ import print_function

import datetime
import time
import dateutil
import re
import os
from warnings import warn
import boto3

ec2 = boto3.resource('ec2')
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

def get_lb_tag(lb_arn, tag_name):
    response = elbv2.describe_tags(ResourceArns=[lb_arn])
    tag_descriptions = response['TagDescriptions']
    tags = tag_descriptions[0]['Tags']

    if tags is None:
        return None

    for tag in tags:
        if tag['Key'] == tag_name:
            return tag['Value']

    return None

def get_tag(ec2_instance, tag_name):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
    :param tag_name: A string of the key name you are searching for.

    This method returns None if the ec2 instance currently has no tags
    or if the tag is not found. If the tag is found, it returns the tag
    value.
    """
    if ec2_instance.tags is None:
        return None
    for tag in ec2_instance.tags:
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
        termination_date = get_tag(ec2_instance, 'termination_date')
        if termination_date:
            print("'termination_date' tag found!")
            return termination_date
        instance_name = get_tag(ec2_instance, 'Name')
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
        lifetime = get_tag(ec2_instance, 'lifetime')
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

def delete_target_groups(target_groups):
    """
    :param target_groups: load balancer target groups to be deleted

    Deletes target groups associated with a load balancer that was just deleted
    """
    for target_group in target_groups:
        print(target_group)

def delete_load_balancer(lb_arn, message):
    """
    :param lb_arn: an Amazon Resource Name for a load balancer.
    :param message: string explaining why the load balancer is being deleted.

    Prints a message and terminates a load balancer if LIVEMODE is True.
    Otherwise, print out the ARN of the load balancer that would have been
    deleted.
    """

    # Get the target groups that will be deleted after the load balancer
    target_groups = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)

    output = "REAPER TERMINATION: {1} for load balancer ARN={0}\n".format(lb_arn, message)
    if LIVEMODE:
        output += 'REAPER TERMINATION enabled: deleting load balancer {0}'.format(lb_arn)
        print(output)
        elbv2.delete_load_balancer(lb_arn)
    else:
        output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted load balancer {1}".format(LIVEMODE, lb_arn)
        print(output)

    # delete_target_groups(target_groups)

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
    termination_date = get_tag(ec2_instance, 'termination_date')
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

def terminate_expired_load_balancers(event, context):
    """
    :param event: AWS CloudWatch event; should be a Cloudwatch Scheduled Event.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    """
    improperly_tagged = []
    deleted_load_balancers = []

    load_balancers = elbv2.describe_load_balancers()

    for load_balancer in load_balancers['LoadBalancers']:
        lb_arn = load_balancer['LoadBalancerArn']
        lb_termination_date = get_lb_tag(lb_arn, 'termination_date')

        if lb_termination_date is None:
            print("No termination date found for load balancer {0}".format(lb_arn))
            improperly_tagged.append(lb_arn)
            continue

        if lb_termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(lb_termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(lb_termination_date) - timenow_with_utc()
                    print("Load balancer {0} will be deleted {1} seconds from now, roughly".format(lb_arn, ttl.seconds))
                else:
                    delete_load_balancer(lb_arn, "Load balancer {0} has expired".format(lb_arn))
                    deleted_load_balancers.append(lb_arn)
            except Exception as e:
                print("Unable to parse the termination_date {1} for load balancer {0}".format(lb_arn, lb_termination_date))
                continue
        else:
            continue

# This is the function that a terminator lambda should call periodically to delete instances past their
# termination_date.
def terminate_expired_instances(event, context):
    """
    :param event: AWS CloudWatch event; should be a Cloudwatch Scheduled Event.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    """
    improperly_tagged = []
    deleted_instances = []

    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    print(instances)
    for instance in instances:
        ec2_termination_date = get_tag(instance, 'termination_date')
        if ec2_termination_date is None:
            print("No termination date found for {0}".format(instance.id))
            stop_instance(instance, "EC2 instance has no termination_date")
            improperly_tagged.append(instance)
            continue
        if ec2_termination_date != INDEFINITE:
            try:
                if dateutil.parser.parse(ec2_termination_date) > timenow_with_utc():
                    ttl = dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
                    print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
                else:
                    terminate_instance(instance, "EC2 instance has expired")
                    deleted_instances.append(instance)
            except Exception as e:
                print("Unable to parse the termination_date for {0}".format(instance.id))
                stop_instance(instance, "EC2 instance has invalid termination_date")
                improperly_tagged.append(instance)
                continue
        if ec2_termination_date == INDEFINITE:
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
