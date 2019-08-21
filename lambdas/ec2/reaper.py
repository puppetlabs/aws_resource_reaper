from __future__ import print_function

import re
import os
import boto3

from reaper_class import ResourceReaper

SERVICES = [
    'ec2'
    # 'elb',
    # 'elbv2'
]
LIVE_TERMINATION_MESSAGE = "REAPER TERMINATED {0} with ids {1}"
NOOP_TERMINATION_MESSAGE = "REAPER NOOP: Would have terminated {0} with ids {1}"
STOPPED_MESSAGE = "REAPER STOPPED {0} with ids {1} due to missing or unparsable termination_date tag"
NOOP_STOPPED = "REAPER NOOP: Would have stopped {0} with ids {1} due to missing or unparsable termination_date tag"
IMPROPER_TAGS = "REAPER FOUND {0} with ids {1} are missing termination_date tags!"
LIVEMODE = determine_live_mode()

def determine_live_mode():
    """
    Returns True if LIVEMODE is set to true in the shell environment, False for
    all other cases.
    """
    if 'LIVEMODE' in os.environ:
        return re.search(r'(?i)^true$', os.environ['LIVEMODE']) is not None
    else:
        return False

for service in SERVICES:
    reaper = ResourceReaper(service)
    if service != 'ec2':
        boto_resource = boto3.client(
            service,
            # Variables below for local testing
            # aws_access_key_id=,
            # aws_secret_access_key=,
            # region_name="us-west-1"
        )
        items = reaper.terminate_expired_load_balancers(service, livemode=LIVEMODE)
        if items['deleted']:
            if LIVEMODE:
                print(LIVE_TERMINATION_MESSAGE.format(service, items['deleted']))
            else:
                print(NOOP_TERMINATION_MESSAGE.format(service, items['deleted']))
        if items['improperly_tagged']:
            print(IMPROPER_TAGS.format(service, items['improperly_tagged']))
        if 'elbv2' in service:
            target_groups = reaper.terminate_expired_target_groups(service, livemode=LIVEMODE)
            if target_groups['deleted']:
                if LIVEMODE:
                    print(LIVE_TERMINATION_MESSAGE.format('target group', target_groups['deleted']))
                else:
                    print(NOOP_TERMINATION_MESSAGE.format('target group', target_groups['deleted']))
            if target_groups['improperly_tagged']:
                print(IMPROPER_TAGS.format('target group', target_groups['improperly_tagged']))
    elif 'ec2' in service:
        boto_resource = boto3.resource(
            service,
            # Variables below for local testing
            # aws_access_key_id=,
            # aws_secret_access_key=,
            # region_name="us-west-1"
        )
        resources = [
            'instances',
            'internet_gateways',
            'route_tables',
            'network_acls',
            'network_interfaces',
            'security_groups',
            'subnets',
            'vpcs'
        ]
        for resource in resources:
            items = reaper.terminate_expired_ec2_resources(boto_resource, resource, livemode=LIVEMODE)
            if items['deleted']:
                if LIVEMODE:
                    print(LIVE_TERMINATION_MESSAGE.format(resource, items['deleted']))
                else:
                    print(NOOP_TERMINATION_MESSAGE.format(resource, items['deleted']))
            if items['improperly_tagged']:
                print(IMPROPER_TAGS.format(resource, items['improperly_tagged']))
            if items['stopped']:
                if LIVEMODE:
                    print(STOPPED_MESSAGE.format(resource, items['stopped']))
                else:
                    print(NOOP_STOPPED.format(resource, items['stopped']))
