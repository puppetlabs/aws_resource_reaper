from __future__ import print_function

import json
import boto3
import datetime
import time
import dateutil
import re
import os
from warnings import warn

ec2 = boto3.resource('ec2')

# The `LIVE_MODE` environment variable controls if this script is actually
# running and reaping in your AWS environment. To turn reaping on, supply
# a value for the `LIVE_MODE` environment variable in your Lambda environment.
LIVE_MODE = "LIVE_MODE" in os.environ

# The `MINUTES_TO_WAIT` global variable is the number of minutes to wait for
# a termination_date tag to appear for the EC2 instance. Please note that the
# AWS Lambdas are limited to a 5 minute maximum for their total run time.
MINUTES_TO_WAIT = 4

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

    This method exits when either a termination date is found or the wait_time
    has passed. The method looks for the 'lifetime' key, parses it, and sets
    the 'termination_date' on the instance.
    """
    start = timenow_with_utc()
    timeout = start + datetime.timedelta(minutes=wait_time)

    while timenow_with_utc() < timeout:
        ec2_instance.load()
        if get_tag(ec2_instance, 'termination_date'):
            print("'termination_date' tag found!")
            break
        if get_tag(ec2_instance, 'lifetime') is None:
            print("No 'lifetime' tag found; sleeping for 15s")
            time.sleep(15)
            continue
        print('lifetime tag found')
        lifetime = get_tag(ec2_instance,'lifetime')

        try:
            lifetime_delta = calculate_lifetime_delta(lifetime)
        except ValueError as e:
            terminate_and_raise_message(ec2_instance,
                                        'Unable to parse the lifetime value',
                                        exception=e)

        termination_date = start + lifetime_delta
        ec2_instance.create_tags(
                Tags=[
                    {
                        'Key': 'termination_date',
                        'Value': termination_date.isoformat()
                    }
                ]
            )
    else:
        terminate_and_raise_message(ec2_instance,
                                    'No termination_date found within {0} minutes of creation'.format(wait_time))


def terminate_and_raise_message(ec2_instance, message, exception=None, live_mode=LIVE_MODE):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
    :param message: a string of the message you would like to raise
    :param exception: raise new base exception class if none, otherwise raise original exception.
    :param live_mode: defaults to global LIVE_MODE. Determines whether to actually delete instance or not.

    Prints a message and terminates an instance if LIVE_MODE is True. Otherwise, just print out
    the instance id of EC2 resource that would have been deleted.
    """
    if live_mode:
        print("Deleting instance {0}".format(ec2_instance.id))
        ec2_instance.terminate()
    else:
        print("LIVE_MODE not enabled: would have deleted instance {0}".format(ec2_instance.id))

    if exception is None:
        raise Exception(message)
    else:
        Warning(message)
        raise

def validate_ec2_termination_date(ec2_instance):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.

    Validates that an ec2 instance has a valid termination_date in the future.
    """
    ec2_termination_date = get_tag(ec2_instance, 'termination_date')
    if ec2_termination_date is None:
        terminate_and_raise_message(ec2_instance, 'No termination_date found; EC2 instance to be deleted')
    try:
        dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
    except Exception as e:
        if e is TypeError:
            if re.search(r'(offset-naive).+(offset-aware)', e.__str__):
                terminate_and_raise_message(ec2_instance,
                                            'The termination_date requires a UTC offset',
                                            exception=e)
            else:
                terminate_and_raise_message(ec2_instance,
                                            'Unable to parse the termination_date',
                                            exception=e)
        raise

    if dateutil.parser.parse(ec2_termination_date) > timenow_with_utc():
        ttl = dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
        print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
    else:
        terminate_and_raise_message(ec2_instance,
                                    'The termination_date has passed')



def calculate_lifetime_delta(lifetime_value):
    """
    :param lifetime_value: string that with an integer and a single letter unit of (w)eeks, (d)ays, or (h)ours.

    Takes a string of of an integer value with a 1 letter unit of w(weeks), d(days), h(hours).
    """
    regex = r'^([0-9]+)(w|d|h)$'
    match = re.search(regex, lifetime_value)
    if match is None:
        warn("Invalid lifetime syntax; please provide valid integer followed by 1 letter unit: 1w=1week, 2d=2day, 3h=3hours.")
        raise ValueError("The lifetime value {0} cannot be parsed".format(lifetime_value))

    toople = match.groups()
    unit = toople[1]
    length = int(toople[0])
    if unit == 'w':
        return datetime.timedelta(weeks=length)
    elif unit =='h':
        return datetime.timedelta(hours=length)
    elif unit == 'd':
        return datetime.timedelta(days=length)
    else:
        raise ValueError("Unable to parse the unit '{0}'".format(unit))


def lambda_handler(event, context):
    print(event)
    print(event['detail']['instance-id'])
    instance = ec2.Instance(id=event['detail']['instance-id'])
    try:
        wait_for_tags(instance, MINUTES_TO_WAIT)
        validate_ec2_termination_date(instance)
    except Exception as e:
        instance.load()

        if instance.state['Name'] == 'terminated':
            warn('Instance {0} has been terminated'.format(instance.id))
        elif instance.state['Name'] == 'shutting-down':
            warn('Instance {0} is shutting down and likely to be terminated'.format(instance.id))
        elif instance.state['Name'] == 'running':
            if LIVE_MODE:
                warn('Instance {0} is still running due to an unhandled exception'.format(instance.id))
            else:
                warn('Instance {0} is still running, likely because LIVE_MODE is off'.format(instance.id))
        else:
            warn('Instance {0} current state is {1}. This is an unexpected state and should be investigated!'.format(instance.id, instance.state['Name']))
        # TODO: add in code to alert somebody exception happened, or remove
        # this comment if cloudwatch starts watching for exceptions from
        # this lambda
        raise

    print('Schema successfully enforced.')
