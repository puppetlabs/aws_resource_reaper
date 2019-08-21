from __future__ import print_function

import datetime
import time
import dateutil
import re
import os
from warnings import warn

class ResourceReaper(object):
    """A class for AWS resources that need automatically
    deprovisioned.
    """
    def __init__(self, service):
        self.service_name = service
        self.prod_infra = 'prod_infra'

    def get_tag(self, tag_array, tag_name):
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

    def timenow_with_utc(self):
        """
        Return a datetime object that includes the tzinfo for utc time.
        """
        time = datetime.datetime.utcnow()
        time = time.replace(tzinfo=dateutil.tz.tz.tzutc())
        return time

    def wait_for_tags(self, ec2_instance, wait_time):
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
        start = self.timenow_with_utc()
        timeout = start + datetime.timedelta(minutes=wait_time)

        while self.timenow_with_utc() < timeout:
            ec2_instance.load()
            termination_date = self.get_tag(ec2_instance.tags, 'termination_date')
            if termination_date:
                print("'termination_date' tag found!")
                return termination_date
            lifetime = self.get_tag(ec2_instance.tags, 'lifetime')
            if not lifetime:
                print("No 'lifetime' tag found; sleeping for 15s")
                time.sleep(15)
                continue
            print('lifetime tag found')
            if lifetime == self.prod_infra:
                ec2_instance.create_tags(
                    Tags=[
                        {
                            'Key': 'termination_date',
                            'Value': self.prod_infra
                        }
                    ]
                )
                return
            lifetime_match = self.validate_lifetime_value(lifetime)
            if not lifetime_match:
                self.terminate_instance(ec2_instance, 'Invalid lifetime value supplied')
                return
            lifetime_delta = self.calculate_lifetime_delta(lifetime_match)
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
        self.terminate_instance(ec2_instance,
                        'No termination_date found within {0} minutes of creation'.format(wait_time))

    def delete_resource(self, service, resource, resource_id):
        """Deletes an arbitrary AWS resource. Is currently only
        set up and being used for EC2 resources

        :param service: An AWS service, like EC2, S3
        :param resource: An AWS service resource, like instance or subnet
        :param resource_id: The AWS id of a resource

        Returns None
        """
        # Resources get passed through in snake case, need converted to camel
        # case for use as attributes
        if "_" in resource:
            resource = resource.replace("_", " ")
            resource = resource.title()
            resource = resource.replace(" ", "")
            resource = resource[:-1]
        else:
            resource = resource.title()
            resource = resource[:-1]
        # Sets item to the resource we want to delete
        item = getattr(service,resource)(resource_id)
        if "RouteTable" in resource:
            route_table_associations = item.associations_attribute
            for route_table_association in route_table_associations:
                if not route_table_association.main:
                    rta_id = route_table_association['RouteTableAssociationId']
                    rta = service.RouteTableAssociation(rta_id)
                    rta.delete()
        elif "NetworkACL" in resource:
            if not item.is_default:
                item.delete()
        elif "Instance" in resource:
            item.terminate()
            waiter = service.meta.client.get_waiter('instance_terminated')
            waiter.wait(InstanceIds=[item.id])
        else:
            item.delete()

    def delete_target_group(self, service, tg_arn):
        service.delete_target_group(TargetGroupArn=tg_arn)

    def delete_load_balancer(self, service, lb_name):
        """
        :param lb_name: a classic load balancer name
        :param message: string explaining why the load balancer is being deleted.

        Prints a message and terminates a load balancer if LIVEMODE is True.
        Otherwise, print out the name of the load balancer that would have been
        deleted.
        """
        service.delete_load_balancer(LoadBalancerName=lb_name)

    def delete_v2_load_balancer(self, service, lb_arn):
        """
        :param lb_arn: an Amazon Resource Name for a load balancer.
        :param message: string explaining why the load balancer is being deleted.

        Prints a message and terminates a load balancer if LIVEMODE is True.
        Otherwise, print out the ARN of the load balancer that would have been
        deleted.
        """
        service.delete_load_balancer(LoadBalancerArn=lb_arn)

        # Wait for the load balancer to be deleted
        waiter = service.get_waiter('load_balancers_deleted')
        waiter.wait(LoadBalancerArns=[lb_arn])

    def terminate_instance(self, ec2_instance, resource):
        """
        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
        :param message: string explaining why the instance is being terminated.

        Prints a message and terminates an instance if LIVEMODE is True. Otherwise, print out
        the instance id of EC2 resource that would have been deleted.
        """
        ec2_instance.terminate()
        waiter = ec2_instance.meta.client.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=[ec2_instance.id])

    def stop_instance(self, service, resource, resource_id):
        """

        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
        :param message: string explaining why the instance is being terminated

        Prints a message and stop an instance if LIVEMODE is True. Otherwise, print out
        the instance id of the EC2 resource that would have been deleted
        """
        resource = resource.title()
        resource = resource[:-1]
        item = service.resource(resource_id)
        item.stop()

    def validate_ec2_termination_date(self, ec2_instance):
        """
        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.

        Validates that an ec2 instance has a valid termination_date in the future.
        Otherwise, delete the instance.
        """
        termination_date = self.get_tag(ec2_instance.tags, 'termination_date')
        try:
            dateutil.parser.parse(termination_date) - self.timenow_with_utc()
        except Exception as e:
            if e is TypeError:
                if re.search(r'(offset-naive).+(offset-aware)', e.__str__):
                    self.terminate_instance(ec2_instance,
                                    'The termination_date requires a UTC offset')
                else:
                    self.terminate_instance(ec2_instance,
                                    'Unable to parse the termination_date')
                return

        if dateutil.parser.parse(termination_date) > self.timenow_with_utc():
            ttl = dateutil.parser.parse(termination_date) - self.timenow_with_utc()
            print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
        else:
            self.terminate_instance(ec2_instance,
                            'The termination_date has passed')

    def validate_lifetime_value(self, lifetime_value):
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

    def calculate_lifetime_delta(self, lifetime_tuple):
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
    def enforce(self, event, context, resource, wait_time):
        """
        :param event: AWS CloudWatch event; should be a configured for when the state is pending.
        :param context: Object to determine runtime info of the Lambda function.

        See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
        on context.
        """
        print(event)
        print(event['detail']['instance-id'])
        instance = resource.Instance(id=event['detail']['instance-id'])
        try:
            termination_date = self.wait_for_tags(instance, wait_time)
            if termination_date == self.prod_infra:
                return
            elif termination_date:
                self.validate_ec2_termination_date(instance)
        except:
            # Here we should catch all exceptions, report on the state of the instance, and then
            # bubble up the original exception.
            instance.load()
            warn('Instance {0} current state is {1}. This unexpected exception should be investigated!'.format(instance.id, instance.state['Name']))
            #  TODO: add in code to alert somebody exception happened, or remove
            # this comment if cloudwatch starts watching for exceptions from
            # this lambda
            raise

        print('Schema successfully enforced.')

    def terminate_expired_ec2_resources(self, service, resource, livemode):
        improperly_tagged = []
        deleted = []
        stopped = []
        if 'instances' in resource:
            resources = getattr(service,resource).filter(
                Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
        elif 'target_groups' in resource:
            target_groups = service.describe_target_groups()
            resources = target_groups['TargetGroups']
        else:
            resources = getattr(service,resource).all()
        for item in resources:
            if 'target_groups' in resource:
                resource_id = item['TargetGroupArn']
                tags = service.describe_tags(ResourceArns=[resource_id])
                tag_descriptions = tags['TagDescriptions']
                tag_array = tag_descriptions[0]['Tags']
                termination_date = self.get_tag(tag_array, 'termination_date')
            elif 'network_interfaces' in resource:
                resource_id = item.id
                termination_date = self.get_tag(item.tag_set, 'termination_date')
            else:
                resource_id = item.id
                termination_date = self.get_tag(item.tags, 'termination_date')

            if termination_date is None:
                improperly_tagged.append(resource_id)
                if 'instances' in resource:
                    stopped.append(resource_id)
                    if livemode:
                        self.stop_instance(service, resource, resource_id)
                continue

            if termination_date != self.prod_infra:
                try:
                    if dateutil.parser.parse(termination_date) > self.timenow_with_utc():
                        ttl = dateutil.parser.parse(termination_date) - self.timenow_with_utc()
                        print("{0} {1} will be deleted {2} seconds from now, roughly".format(resource, resource_id, ttl.seconds))
                    else:
                        if livemode:
                            self.delete_resource(service, resource, resource_id)
                        deleted.append(resource_id)
                except:
                    print("Unable to parse the termination_date {0} for {1} {2}".format(termination_date, resource, resource_id))
                    continue
            else:
                continue
        resource_data = {
            'deleted': deleted,
            'improperly_tagged': improperly_tagged,
            'stopped': stopped
        }
        return resource_data

    def terminate_expired_load_balancers(self, service, livemode):
        improperly_tagged = []
        deleted_load_balancers = []

        load_balancers = service.describe_load_balancers()

        if 'elbv2' in service:
            load_balancer_info = load_balancers['LoadBalancerDescriptions']
        else:
            load_balancer_info = load_balancers['LoadBalancers']
        for load_balancer in load_balancer_info:
            if 'elbv2' in service:
                lb_name = load_balancer['LoadBalancerArn']
                tags = service.describe_tags(ResourceArns=[lb_arn])
            else:
                lb_name = load_balancer['LoadBalancerName']
                tags = service.describe_tags(LoadBalancerNames=[lb_name])
            tag_descriptions = tags['TagDescriptions']
            tag_array = tag_descriptions[0]['Tags']
            termination_date = self.get_tag(tag_array, 'termination_date')

            if termination_date is None:
                improperly_tagged.append(lb_name)
                continue

            if termination_date != self.prod_infra:
                try:
                    if dateutil.parser.parse(termination_date) > self.timenow_with_utc():
                        ttl = dateutil.parser.parse(termination_date) - self.timenow_with_utc()
                        print("Load balancer {0} will be deleted {1} seconds from now, roughly".format(lb_name, ttl.seconds))
                    else:
                        if 'elbv2' in service:
                            if livemode:
                                self.delete_v2_load_balancer(service, lb_name)
                        else:
                            if livemode:
                                self.delete_load_balancer(service, lb_name)
                        deleted_load_balancers.append(lb_name)
                except:
                    print("Unable to parse the termination_date {1} for load balancer {0}".format(lb_name, termination_date))
                    continue
            else:
                continue
        elb_info = {
            'deleted': deleted_load_balancers,
            'improperly_tagged': improperly_tagged
        }
        return elb_info

    def terminate_expired_target_groups(self, service, livemode):
        improperly_tagged = []
        deleted_target_groups = []

        # Get the target groups that will be deleted after the load balancer
        target_groups = service.describe_target_groups()
        target_groups_array = target_groups['TargetGroups']
        for target_group in target_groups_array:
            tg_arn = target_group['TargetGroupArn']
            tags = service.describe_tags(ResourceArns=[tg_arn])
            tag_descriptions = tags['TagDescriptions']
            tag_array = tag_descriptions[0]['Tags']
            termination_date = self.get_tag(tag_array, 'termination_date')

            if termination_date is None:
                improperly_tagged.append(tg_arn)
                continue

            if termination_date != self.prod_infra:
                try:
                    if dateutil.parser.parse(termination_date) > self.timenow_with_utc():
                        ttl = dateutil.parser.parse(termination_date) - self.timenow_with_utc()
                        print("Target group {0} will be deleted {1} seconds from now, roughly".format(tg_arn, ttl.seconds))
                    else:
                        if livemode:
                            self.delete_target_group(service, tg_arn)
                        deleted_target_groups.append(tg_arn)
                except:
                    print("Unable to parse the termination_date {1} for target group {0}".format(tg_arn, termination_date))
                    continue
            else:
                continue
        target_group_info = {
            'deleted': deleted_target_groups,
            'improperly_tagged': improperly_tagged
        }
        return target_group_info
