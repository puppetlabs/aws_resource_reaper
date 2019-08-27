import datetime
import time
import re
import dateutil


class ResourceReaper:
    """A class for managing AWS resources that need
    automatically deprovisioned.

    :param service: The AWS service that needs managed
    :param livemode: Whether or not resources should actually be deleted
    :param wait_time: Time to wait for tags to populate for EC2 instances
    :param prod_infra: Tag for resources that should never expire
    """

    def __init__(self, service, livemode):
        self.service = service
        self.livemode = livemode
        self.wait_time = 4
        self.prod_infra = "indefinite"

    def get_tag(self, tag_array, tag_name):
        """
        :param tag_array: an array of tags with Key/Value pairs.
        :param tag_name: a string of the key name you are searching for.

        This method returns None if the ec2 instance currently has no tags
        or if the tag is not found. If the tag is found, it returns the tag
        value.
        """
        if tag_array is None:
            output = None
        elif tag_array == []:
            output = None
        else:
            for tag in tag_array:
                if tag["Key"] == tag_name:
                    output = tag["Value"]
                    break
                else:
                    output = None
        return output

    def timenow_with_utc(self):
        """
        Return a datetime object that includes the tzinfo for utc time.
        """
        timenow = datetime.datetime.utcnow()
        timenow = timenow.replace(tzinfo=dateutil.tz.tz.tzutc())
        return timenow

    def wait_for_tags(self, ec2_instance):
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
        timeout = start + datetime.timedelta(minutes=self.wait_time)

        while self.timenow_with_utc() < timeout:
            ec2_instance.load()
            termination_date = self.get_tag(ec2_instance.tags, "termination_date")
            if termination_date:
                print("'termination_date' tag found!")
                return termination_date
            lifetime = self.get_tag(ec2_instance.tags, "lifetime")
            if not lifetime:
                print("No 'lifetime' tag found; sleeping for 15s")
                time.sleep(15)
                continue
            if lifetime == self.prod_infra:
                ec2_instance.create_tags(
                    Tags=[{"Key": "termination_date", "Value": self.prod_infra}]
                )
                return
            lifetime_match = self.validate_lifetime_value(lifetime)
            if not lifetime_match:
                output = self.terminate_instance(ec2_instance, "Invalid lifetime value supplied")
                print(output)
                return
            lifetime_delta = self.calculate_lifetime_delta(lifetime_match)
            future_termination_date = start + lifetime_delta
            ec2_instance.create_tags(
                Tags=[
                    {
                        "Key": "termination_date",
                        "Value": future_termination_date.isoformat(),
                    }
                ]
            )
            print("'termination_date' tag created!")
            return

        # If the above while condition does not return after finding a termination_date,
        # terminate the instance and raise an exception.
        output = self.terminate_instance(
            ec2_instance,
            "No termination_date found within {0} minutes of creation".format(
                self.wait_time
            ),
        )
        print(output)

    def adjust_resource_string(self, resource):
        """
        Resources get passed through in snake case, need converted to camel
        case for use as attributes by the boto3 library

        :param resource: An aws resource like an internet_gateway

        Returns:
            String with correct camel casing
        """
        if "_" in resource:
            resource_string = resource.replace("_", " ")
            resource_string = resource_string.title()
            resource_string = resource_string.replace(" ", "")
            resource_string = resource_string[:-1]
        else:
            resource_string = resource.title()
            resource_string = resource_string[:-1]
        return resource_string

    def delete_ec2_resource(self, resource, resource_id):
        """Deletes an arbitrary AWS resource. Is currently only
        set up and being used for EC2 resources

        :param service: An AWS service, like EC2, S3
        :param resource: An AWS service resource, like instance or subnet
        :param resource_id: The AWS id of a resource

        Returns None
        """
        resource = self.adjust_resource_string(resource)
        # Sets item to the resource we want to delete
        item = getattr(self.service, resource)(resource_id)
        if "RouteTable" in resource:
            route_table_associations = item.associations_attribute
            for route_table_association in route_table_associations:
                if not route_table_association.main:
                    rta_id = route_table_association["RouteTableAssociationId"]
                    rta = self.service.RouteTableAssociation(rta_id)
                    rta.delete()
        elif "NetworkACL" in resource:
            if not item.is_default:
                item.delete()
        elif "Instance" in resource:
            item.terminate()
            waiter = self.service.meta.client.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[item.id])
        elif "InternetGateway" in resource:
            item = getattr(self.service, resource)(resource_id)
            attachments = item.attachments
            vpcs_to_detach = [vpc['VpcId'] for vpc in attachments]
            for vpc in vpcs_to_detach:
                item.detach_from_vpc(VpcId=vpc)
            item.delete()
        else:
            item.delete()

    def delete_target_group(self, tg_arn):
        """
        Deletes a target_group resource

        :param tg_arn: The amazon resource name for the target group
        """
        self.service.delete_target_group(TargetGroupArn=tg_arn)

    def delete_load_balancer(self, lb_name):
        """
        :param lb_name: a classic load balancer name
        :param message: string explaining why the load balancer is being deleted.

        Prints a message and terminates a load balancer if LIVEMODE is True.
        Otherwise, print out the name of the load balancer that would have been
        deleted.
        """
        self.service.delete_load_balancer(LoadBalancerName=lb_name)

    def delete_v2_load_balancer(self, lb_arn):
        """
        :param lb_arn: an Amazon Resource Name for a load balancer.
        :param message: string explaining why the load balancer is being deleted.

        Prints a message and terminates a load balancer if LIVEMODE is True.
        Otherwise, print out the ARN of the load balancer that would have been
        deleted.
        """
        self.service.delete_load_balancer(LoadBalancerArn=lb_arn)

        # Wait for the load balancer to be deleted
        waiter = self.service.get_waiter("load_balancers_deleted")
        waiter.wait(LoadBalancerArns=[lb_arn])

    def terminate_instance(self, ec2_instance, message):
        """
        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
        :param message: string explaining why the instance is being terminated.

        Prints a message and terminates an instance if LIVEMODE is True. Otherwise, print out
        the instance id of EC2 resource that would have been deleted.
        """
        output = "REAPER TERMINATION: {1} for ec2_instance_id={0}\n".format(
            ec2_instance.id, message
        )
        if self.livemode:
            output += "REAPER TERMINATION enabled: deleting instance {0}".format(
                ec2_instance.id
            )
            ec2_instance.terminate()
            waiter = self.service.meta.client.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[ec2_instance.id])
        else:
            output += "REAPER TERMINATION not enabled: LIVEMODE is {0}. Would have deleted instance {1}".format(
                self.livemode, ec2_instance.id
            )
        return output

    def stop_instance(self, resource, resource_id):
        """

        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
        :param message: string explaining why the instance is being terminated

        Prints a message and stop an instance if LIVEMODE is True. Otherwise, print out
        the instance id of the EC2 resource that would have been deleted
        """
        resource = resource.title()
        resource = resource[:-1]
        item = getattr(self.service,resource)(resource_id)
        item.stop()

    def validate_ec2_termination_date(self, ec2_instance):
        """
        :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.

        Validates that an ec2 instance has a valid termination_date in the future.
        Otherwise, delete the instance.
        """
        output = None
        termination_date = self.get_tag(ec2_instance.tags, "termination_date")
        try:
            dateutil.parser.parse(termination_date) - self.timenow_with_utc()
        except Exception as error:
            if error is TypeError:
                if re.search(r"(offset-naive).+(offset-aware)", error.__str__):
                    output = self.terminate_instance(
                        ec2_instance, "The termination_date requires a UTC offset"
                    )
                else:
                    output = self.terminate_instance(
                        ec2_instance, "Unable to parse the termination_date"
                    )

        if not output:
            if dateutil.parser.parse(termination_date) > self.timenow_with_utc():
                ttl = dateutil.parser.parse(termination_date) - self.timenow_with_utc()
                output = (
                    "EC2 instance will be terminated {0} seconds from now, roughly".format(
                        ttl.seconds
                    )
                )
            else:
                output = self.terminate_instance(
                    ec2_instance, "The termination_date has passed"
                )
        return output

    def validate_lifetime_value(self, lifetime_value):
        """
        :param lifetime_value: A string from your ec2 instance.

        Return a match object if a match is found; otherwise, return the None from the search method.
        """
        search_result = re.search(r"^([0-9]+)(w|d|h|m)$", lifetime_value)
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
        if unit == "w":
            return datetime.timedelta(weeks=length)
        elif unit == "h":
            return datetime.timedelta(hours=length)
        elif unit == "d":
            return datetime.timedelta(days=length)
        elif unit == "m":
            return datetime.timedelta(minutes=length)
        else:
            raise ValueError("Unable to parse the unit '{0}'".format(unit))

    def terminate_expired_ec2_resources(self, resource):
        """
        Get's an AWS EC2 instance and deletes the instance along with
        all resources associated with that instance if the termination_date
        tags are expired.
        Stops instances that don't have a valid termination_date tag
        Put's id's of resources into lists that are printed to the console
        for Slack notifications. Lists are for resources that have:
            No tags or malformed tags
            Been stopped due to missing or malformed tags (ec2 instances)
            Been terminated due to expired termination_date tags

        :param service: An AWS service, like EC2
        :param resource: An AWS service resource, like instance or subnet

        Returns:
            Dict of lists for printing to logs
        """

        # Lists of resource IDs
        improperly_tagged = []
        deleted = []
        stopped = []

        # If statement to get resource ID's based on resource given.
        # Instances portion gets only running instance IDs
        if "instances" in resource:
            resources = getattr(self.service, resource).filter(
                Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
            )
        else:
            resources = getattr(self.service, resource).all()
        # Get termination date tag for the resource given
        for item in resources:
            if "network_interfaces" in resource:
                resource_id = item.id
                termination_date = self.get_tag(item.tag_set, "termination_date")
            else:
                resource_id = item.id
                termination_date = self.get_tag(item.tags, "termination_date")

            # If termination date is None, add ID to improperly tagged list,
            # if resource is ec2 instance stop it
            if termination_date is None:
                improperly_tagged.append(resource_id)
                if "instances" in resource:
                    stopped.append(resource_id)
                    if self.livemode:
                        self.stop_instance(resource, resource_id)
                continue

            # Checks termination_date tag to see if resource is expired
            # Deletes resource if it's termination_date tag is expired,
            # if not then prints time in seconds until expiration
            if termination_date != self.prod_infra:
                try:
                    if (
                        dateutil.parser.parse(termination_date)
                        > self.timenow_with_utc()
                    ):
                        ttl = (
                            dateutil.parser.parse(termination_date)
                            - self.timenow_with_utc()
                        )
                        print(
                            "{0} {1} will be deleted {2} seconds from now, roughly".format(
                                resource, resource_id, ttl.seconds
                            )
                        )
                    else:
                        if self.livemode:
                            self.delete_ec2_resource(resource, resource_id)
                        deleted.append(resource_id)
                except ValueError:
                    print(
                        "Unable to parse the termination_date '{0}' for {1} {2}".format(
                            termination_date, resource, resource_id
                        )
                    )
                    continue
            else:
                continue
        # Dict of lists returned for logging
        resource_data = {
            "deleted": deleted,
            "improperly_tagged": improperly_tagged,
            "stopped": stopped,
        }
        return resource_data

    def terminate_expired_load_balancers(self):
        """
        Gets AWS classic and v2 Elastic Load Balancers from AWS
        and terminates them if they have expired termination_date tags

        Put's id's of resources into lists that are printed to the console
        for Slack notifications. Lists are for resources that have:
            No tags or malformed tags
            Been stopped due to missing or malformed tags (ec2 instances)
            Been terminated due to expired termination_date tags

        Returns:
            Dict of lists for printing to logs
        """

        # Lists of resource IDs
        improperly_tagged = []
        deleted_load_balancers = []

        load_balancers = self.service.describe_load_balancers()

        # Checks whether or not service is elb or elbv2
        if "v2" in str(self.service):
            load_balancer_info = load_balancers["LoadBalancers"]
        else:
            load_balancer_info = load_balancers["LoadBalancerDescriptions"]

        # Gets termination_date tags for load balancers
        for load_balancer in load_balancer_info:
            if "v2" in str(self.service):
                lb_name = load_balancer["LoadBalancerArn"]
                tags = self.service.describe_tags(ResourceArns=[lb_name])
            else:
                lb_name = load_balancer["LoadBalancerName"]
                tags = self.service.describe_tags(LoadBalancerNames=[lb_name])
            tag_descriptions = tags["TagDescriptions"]
            tag_array = tag_descriptions[0]["Tags"]
            termination_date = self.get_tag(tag_array, "termination_date")

            if termination_date is None:
                improperly_tagged.append(lb_name)
                continue

            # Checks termination_date tag to see if it's expired, if it
            # is then deletes load balancer.
            if termination_date != self.prod_infra:
                try:
                    if (
                        dateutil.parser.parse(termination_date)
                        > self.timenow_with_utc()
                    ):
                        ttl = (
                            dateutil.parser.parse(termination_date)
                            - self.timenow_with_utc()
                        )
                        print(
                            "Load balancer {0} will be deleted {1} seconds from now, roughly".format(
                                lb_name, ttl.seconds
                            )
                        )
                    else:
                        if "v2" in str(self.service):
                            if self.livemode:
                                self.delete_v2_load_balancer(lb_name)
                        else:
                            if self.livemode:
                                self.delete_load_balancer(lb_name)
                        deleted_load_balancers.append(lb_name)
                except ValueError:
                    print(
                        "Unable to parse the termination_date '{1}' for load balancer {0}".format(
                            lb_name, termination_date
                        )
                    )
                    continue
            else:
                continue
        elb_info = {
            "deleted": deleted_load_balancers,
            "improperly_tagged": improperly_tagged,
        }
        return elb_info

    def terminate_expired_target_groups(self):
        """
        Gets AWS Target Groups from AWS and terminates them if they have
        expired termination_date tags

        Put's id's of resources into lists that are printed to the console
        for Slack notifications. Lists are for resources that have:
            No tags or malformed tags
            Been stopped due to missing or malformed tags (ec2 instances)
            Been terminated due to expired termination_date tags

        :param service: An AWS service, like ELB
        :param livemode: Whether or not the reaper should delete resources

        Returns:
            Dict of lists for printing to logs
        """

        # Lists of resource IDs
        improperly_tagged = []
        deleted_target_groups = []

        # Get the target groups that will be deleted after the load balancer
        target_groups = self.service.describe_target_groups()
        target_groups_array = target_groups["TargetGroups"]

        # Get the termination_date tags for target groups
        for target_group in target_groups_array:
            tg_arn = target_group["TargetGroupArn"]
            tags = self.service.describe_tags(ResourceArns=[tg_arn])
            tag_descriptions = tags["TagDescriptions"]
            tag_array = tag_descriptions[0]["Tags"]
            termination_date = self.get_tag(tag_array, "termination_date")

            if termination_date is None:
                improperly_tagged.append(tg_arn)
                continue

            # Checks termination_date tag to see if it's expired, if it
            # is then deletes target group
            if termination_date != self.prod_infra:
                try:
                    if (
                        dateutil.parser.parse(termination_date)
                        > self.timenow_with_utc()
                    ):
                        ttl = (
                            dateutil.parser.parse(termination_date)
                            - self.timenow_with_utc()
                        )
                        print(
                            "Target group {0} will be deleted {1} seconds from now, roughly".format(
                                tg_arn, ttl.seconds
                            )
                        )
                    else:
                        if self.livemode:
                            self.delete_target_group(tg_arn)
                        deleted_target_groups.append(tg_arn)
                except ValueError:
                    print(
                        "Unable to parse the termination_date '{1}' for target group {0}".format(
                            tg_arn, termination_date
                        )
                    )
                    continue
            else:
                continue
        target_group_info = {
            "deleted": deleted_target_groups,
            "improperly_tagged": improperly_tagged,
        }
        return target_group_info
