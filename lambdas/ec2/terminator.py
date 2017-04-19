from __future__ import print_function

import json
import boto3
import datetime
import time
import dateutil
import re
import os

ec2 = boto3.resource('ec2')

# The `LIVE_MODE` environment variable controls if this script is actually
# running and reaping in your AWS environment. To turn reaping on, supply
# a value for the `LIVE_MODE` environment variable in your Lambda environment.
LIVE_MODE = "LIVE_MODE" in os.environ

def timenow_with_utc():
    """
    Return a datetime object that includes the tzinfo for utc time.
    """
    time = datetime.datetime.utcnow()
    time = time.replace(tzinfo=dateutil.tz.tz.tzutc())
    return time

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

def lambda_handler(event, context):
    improperly_tagged = []
    deleted_instances = []

    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    for instance in instances:
        if get_tag(instance, 'termination_date') is None:
            print("No termination date found for {0}".format(instance.id))
            improperly_tagged.append(instance)
            continue
        ec2_termination_date = get_tag(instance, 'termination_date')
        try:
            dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
        except Exception as e:
            print("Unable to parse the termination_date for {0}".format(instance.id))
            improperly_tagged.append(instance)
            continue

        if dateutil.parser.parse(ec2_termination_date) > timenow_with_utc():
            ttl = dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
            print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
        else:
            if LIVE_MODE:
                instance.terminate()
                print("Termination Date passed; deleting EC2 instance {0}".format(instance.id))
            else:
                print('LIVE_MODE off, would have deleted EC2 instance {0}'.format(instance.id))
            deleted_instances.append(instance)

    if LIVE_MODE:
        print("The following instances have been deleted:\n{0}".format(deleted_instances))
    else:
        print("LIVE_MODE is off, would have deleted the following instances:\n".format(deleted_instances))
    if improperly_tagged:
        raise ValueError("Instances found with unparsable termination_date tags:\n{0}".format(improperly_tagged))

