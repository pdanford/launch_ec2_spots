#!/usr/bin/env python

"""
Script to launch spot instances in Amazon's EC2 as specified by a launch
specification file.

Note that this script finishing successfully does not mean the instances
have completed booting (e.g. you may need to wait a bit for sshd to start).
It only means the spot instances successfully launched to an active state
(i.e. the spot request has been fulfilled). Use -w to wait for full
initialization. Use -h to see other options.

Requires: A json launch specification file.
          awscli (sudo pip install awscli).
          python 2.7 or above.

Example launch spec file required by this script:
  {
    "INSTANCE_COUNT": "2",
    "MAX_SPOT_PRICE": "0.08",
    "USER_DATA_FILE_NAME":"ami_bootstrap",
    "ImageId": "ami-bba18dd2",
    "InstanceType": "t1.micro",
    "KeyName": "amazon-ssh-pub-key-tag",
    "Placement": { "AvailabilityZone": "us-east-1c", "GroupName": "" },
    "SecurityGroups": ["default"]
  }
Any additional Amazon launch specification defined fields may be added as needed. See
http://docs.aws.amazon.com/cli/latest/reference/ec2/request-spot-instances.html
for more info.

awscli requires the environment variables AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY to be set, or alternatively, the configuration
file ~/.aws/config on Linux/OSX or C:\\Users\\USERNAME\\.aws\\config on Windows.
A minimal example of this file is:
  [default]
  aws_access_key_id = YOUR_ACCESS_KEY
  aws_secret_access_key = YOUR_SECRET_KEY

USER_DATA_FILE_NAME is optional and specifies a file containing user data to be made
available to all launched AMIs. Note that if the user data begins with an interpreter
(e.g. #!/bin/bash), an Amazon supplied AMI will regard the user data as a bootstrap
script and automatically run it (as root) at AMI boot time.

EC2 launch region is derived from:
1) The AWS_DEFAULT_REGION environment variable.
2) Adding a line like 'region = us-east-1' to ~/.aws/config under the [default] section. 
3) Placement:AvailabilityZone specified in launch spec file as in the above example; if
   AvailabilityZone is set to something like "us-east-1" instead of a proper "us-east-1c", 
   then the placement will be in the region us-east-1 and the actual zone will be
   determined by Amazon. In both cases, the region is derived from the AvailabilityZone
   value and overrides any region set by method 1 or 2. Setting a proper AvailabilityZone
   may be desirable when best historical spot price zone is a concern.

Troubleshooting:  

Note that some launches may result in requests never finishing (i.e. becoming zombies).
As an example, if the user data referenced by USER_DATA_FILE_NAME is greater than 16k,
the Amazon request will stall and not return an error. Ctrl-C can be used while the script
is in a waiting state to abort the launch and kill the stalled requests.

Feb, 2014
pdanford@pdanford.com
"""

version = "1.0"

import os
import sys
import json
import time
import base64
import subprocess

def _process_launch_spec(launch_spec_json):
    """
    Processes launch_spec_json string to yield a launch parameters tuple.
    That is, separate pure Amazon json launch spec from args that must be passed
    on the aws client command line.
    Returns (instanceCount, maxSpotPrice, region_switch, amazon_launch_spec_json)
    """
    try:
    	launch_spec = json.loads(launch_spec_json)
    except Exception as err:
        sys.stderr.write("\nThe json launch spec file has a problem at the location indicated below:\n")
        raise

    # Separate the spot price and instance count since these two things are passed as aws cli switches.
    if 'INSTANCE_COUNT' not in launch_spec:
        raise EnvironmentError("INSTANCE_COUNT missing from launch configuration.")
    instanceCount = launch_spec['INSTANCE_COUNT']
    del launch_spec['INSTANCE_COUNT']
    
    if 'MAX_SPOT_PRICE' not in launch_spec:
        raise EnvironmentError("MAX_SPOT_PRICE missing from launch configuration.")
    maxSpotPrice = launch_spec['MAX_SPOT_PRICE']
    del launch_spec['MAX_SPOT_PRICE']
    
    # Try to get the EC2 Region from the AvailabilityZone specified in the spot instance launch specification file.
    # (If it doesn't exist or is "", then the AWS client will look in the config file and environment vars.
    region_switch = ""
    if 'Placement' in launch_spec:
        if launch_spec['Placement']['AvailabilityZone'] != "":
            if launch_spec['Placement']['AvailabilityZone'][-1:].isdigit():
                # No availability zone was specified (only region).
                region_switch = " --region " + launch_spec['Placement']['AvailabilityZone'] + " "
                del launch_spec['Placement']
            else:
                # Specific availability zone was specified. Strip zone and set region to rest of string.
                region_switch = " --region " + launch_spec['Placement']['AvailabilityZone'][:-1] + " "

    # Insert user's AMI user data if specified.
    if 'USER_DATA_FILE_NAME' in launch_spec:
        user_data_file_name = launch_spec['USER_DATA_FILE_NAME']
        del launch_spec['USER_DATA_FILE_NAME']
        if user_data_file_name != "":
            if os.path.isfile(user_data_file_name):
                with open(user_data_file_name, 'r') as f:
                    user_data_string = f.read()
                    launch_spec['UserData'] = (base64.b64encode(user_data_string.encode('utf-8'))).decode('utf-8')
            else:
                raise EnvironmentError("User data file '" + user_data_file_name + "' not found.")

    amazon_launch_spec_json = json.dumps(launch_spec, sort_keys=True, indent=4, separators=(',', ': '))    
    return (instanceCount, maxSpotPrice, region_switch, amazon_launch_spec_json)


def _wait_for_full_initialization(launchedInstanceList, region_switch, print_progress_to_stderr):
    """
    Waits until AWS reports instances are fully initialized (InstanceStatus and SystemStatus have 'passed').
    Only called after spot instance requests have been fulfilled (see _wait_for_launch_requests_to_fulfill()).
    """
    
    if print_progress_to_stderr:
        sys.stderr.write('wait_for_full_initialization..')
        sys.stderr.flush()

    wait = True        
    while wait:
        if print_progress_to_stderr:
            sys.stderr.write('.')
            sys.stderr.flush()

        time.sleep(2) # Don't flood Amazon with status requests.
        cmd = "aws " + region_switch + " ec2 describe-instance-status"
        statuses = json.loads(subprocess.check_output(cmd, shell=True, universal_newlines=True))
        oks = 0
        for launchedInstance in launchedInstanceList:
            for status in statuses['InstanceStatuses']:
                if (launchedInstance['InstanceId'] == status['InstanceId'] and
                    status['SystemStatus']['Status'] != 'initializing' and
                    status['InstanceStatus']['Status'] != 'initializing'):
                    oks += 1

        if oks == len(launchedInstanceList):
            wait = False


def _wait_for_launch_requests_to_fulfill(sirIDList, region_switch, print_progress_to_stderr):
    """
    Spot instance requests may spend quite some time waiting before instances are actually launched
    (fulfilled). This waits for all spot instance requests in sirIDList to reach a fulfilled state (or
    a failed launch in which case an EnvironmentError exception is raised).
    """
    if print_progress_to_stderr:
        sys.stderr.write("Waiting for spot instances to launch..")
        
    sirWaitingCount = len(sirIDList)
    while sirWaitingCount > 0:
        if print_progress_to_stderr:
            sys.stderr.write('.')
            sys.stderr.flush()
            
        time.sleep(2) # Don't flood Amazon with status requests.
        cmd = "aws " + region_switch + " ec2 describe-spot-instance-requests"
        requestsData = json.loads(subprocess.check_output(cmd, shell=True, universal_newlines=True))    
        sirWaitingCount = len(sirIDList) # Reset for new requestsData examination.
        if requestsData != "":
            for instanceRequest in requestsData['SpotInstanceRequests']:
                if instanceRequest['SpotInstanceRequestId'] in sirIDList:
                    if instanceRequest['Status']['Code'] == 'fulfilled':
                        sirWaitingCount -= 1
                    elif (instanceRequest['Status']['Code'] == 'constraint-not-fulfillable' or
                          instanceRequest['Status']['Code'] == 'capacity-not-available' or
                          instanceRequest['Status']['Code'] == 'az-group-constraint' or
                          instanceRequest['Status']['Code'] == 'placement-group-constraint' or
                          instanceRequest['Status']['Code'] == 'capacity-oversubscribed' or
                          instanceRequest['Status']['Code'] == 'launch-group-constraint'):
                        # Note that these states are not terminal according to Amazon, but
                        # in practice they will never come out of a holding state (as of 3/2014).
                        # So cancel all to prevent a buildup of unfulfillable open requests.
                        # See http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances-bid-status.html
                        cmd = "aws " + region_switch + " ec2 cancel-spot-instance-requests --spot-instance-request-ids " + " ".join(sirIDList)
                        subprocess.check_output(cmd, shell=True)
                        raise EnvironmentError(instanceRequest['Status']['Code'], instanceRequest['Status']['Message'])
                    elif (instanceRequest['Status']['Code'] == 'system-error' or  # These are terminal states.
                          instanceRequest['Status']['Code'] == 'bad-parameters' or
                          instanceRequest['Status']['Code'] == 'schedule-expired' or
                          instanceRequest['Status']['Code'] == 'canceled-before-fulfillment'):
                        raise EnvironmentError(instanceRequest['Status']['Code'], instanceRequest['Status']['Message'])


def launch_EC2_spot_instances(launch_spec_json, wait_for_full_initialization, print_progress_to_stderr = False):
    """
    Launches EC2 spot instances.
    Returns a list of AMIs that launched as a result of the current request in the form of:
    [{"InstanceId":"i-xxxxxx","PublicIpAddress":"xxx:xxx:xxx:xxx", "PrivateIpAddress":"xxx:xxx:xxx:xxx"}, ...]
    that launched to a running state as specified by launch_spec_json (i.e. does not include IPs of other AMIs
    that may already be running from other requests). See launch spec example at top and --help for more details.
    Raises EnvironmentError if launch fails. Catch Exception for exceptions coming from within the aws client.
    """    
    # Process launch specification.
    instanceCount, maxSpotPrice, region_switch, amazon_launch_spec_json = _process_launch_spec(launch_spec_json)

    if print_progress_to_stderr:
        sys.stderr.write("Stand by.\r")
        sys.stderr.flush()

    # Launch AMI instance(s) via spot request.
    with open('amils_temp.json', 'w') as outfile:
        outfile.write(amazon_launch_spec_json)        
    cmd = "aws " + region_switch + " ec2 request-spot-instances --instance-count " + instanceCount + \
          " --spot-price " + maxSpotPrice + " --launch-specification file://amils_temp.json"
    sirData = json.loads(subprocess.check_output(cmd, shell=True, universal_newlines=True))
    os.remove("amils_temp.json")
    
    # Make a list of spot instance request IDs to match against running AMI instances.
    sirIDList = [sir['SpotInstanceRequestId']  for sir in sirData['SpotInstanceRequests']]

    # Wait for all instances from this spot request to launch.
    try:
        _wait_for_launch_requests_to_fulfill(sirIDList, region_switch, print_progress_to_stderr)
    except (KeyboardInterrupt) as err:
        # Clean up any pending apparently good or zombied requests.
        cmd = "aws " + region_switch + " ec2 cancel-spot-instance-requests --spot-instance-request-ids " + " ".join(sirIDList)
        subprocess.check_output(cmd, shell=True)
        raise

    # Get IPs of instances just successfully launched.
    cmd = "aws " + region_switch + " ec2 describe-instances"
    instancesData = json.loads(subprocess.check_output(cmd, shell=True, universal_newlines=True))
    launchedInstanceList = [
      {'InstanceId':instance['InstanceId'], 'PublicIpAddress':instance['PublicIpAddress'], 'PrivateIpAddress':instance['PrivateIpAddress']}
      for reservation in instancesData['Reservations']  for instance in reservation['Instances']  if ('SpotInstanceRequestId' in instance and
                                                                                                      instance['SpotInstanceRequestId'] in sirIDList)  ]
    if wait_for_full_initialization:
        _wait_for_full_initialization(launchedInstanceList, region_switch, print_progress_to_stderr)
            
    if print_progress_to_stderr:
        sys.stderr.write('done.\n')
        sys.stderr.flush()

    return launchedInstanceList


if __name__ == '__main__':
    import argparse

    class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
        pass

    parser = argparse.ArgumentParser(description='Amazons EC2 Spot Instance Launcher',
                                     formatter_class=CustomFormatter,
                                     epilog=__doc__)

    parser.add_argument('-l', dest='launch_spec_file', default='launch_spec.json',
                        help="json file specifying spot launch request parameters.")

    parser.add_argument('-w', '--wait', action="store_true",
                        help="wait until AWS reports a passed status for all launched AMIs. The default is \
                              to wait only until the 'initializing' stage which means the spot request was \
                              successful and the AMI will or is booting; things like sshd may not be up \
                              yet. However, using this option may cause a longer wait than necessary (e.g. \
                              sshd is actaully up on the launched instance(s), but AWS shows the instance(s) \
                              as still initializing for another minute or so)")

    parser.add_argument('-p', '--progress', action="store_true",
                        help="print progress to stderr")

    parser.add_argument('--ppip', action="store_true",
                        help="print AMI instance private IPs instead of the public IPs to stdout")

    parser.add_argument('-v','--version', action="store_true",
                        help="print version number")

    args = parser.parse_args()

    if args.version:
        sys.stderr.write("launch_ec2_spots.py version " + version + "\n")
        os._exit(0)

    # Load launch specification.
    if os.path.isfile(args.launch_spec_file):
        with open(args.launch_spec_file, 'r') as f:
            launch_spec_json = f.read()
    else:
        sys.stderr.write("\nERROR: Spot instance launch specification file '" + args.launch_spec_file + "' not found!\n\n")
        os._exit(1)
            
    try:
        launchedInstanceList = launch_EC2_spot_instances(launch_spec_json, args.wait, args.progress)
        for instance in launchedInstanceList:
            if args.ppip:
                print(instance['InstanceId'] + " \t" + instance['PrivateIpAddress'])        
            else:
                print(instance['InstanceId'] + " \t" + instance['PublicIpAddress'])
    except (EnvironmentError) as err:
        sys.stderr.write("ERROR.\n")
        for message in err.args:
            sys.stderr.write('[')
            sys.stderr.write(message)
            sys.stderr.write('] ')
        sys.stderr.write("\nAny pending spot requests from this have been canceled to avoid zombies.\n")
        sys.stderr.write("\n\n")
        sys.stderr.flush()
        try:
    	    os.remove("amils_temp.json")
        except:
            pass
        os._exit(1)
    except (KeyboardInterrupt) as err:
        time.sleep(1) # Wait for boto thread to dump its Traceback crud first.
        sys.stderr.write("\n\n*** KeyboardInterrupt - aborting launch ***\n\n")
        sys.stderr.write("Any pending spot requests are in an unknown state.\n\n\n")
        sys.stderr.flush()
        try:
    	    os.remove("amils_temp.json")
        except:
            pass
        os._exit(1)
    except Exception as err:
        # aws client will print error description.
        try:
            sys.stderr.write(err.args[0])
            sys.stderr.flush()
        except Exception:
            pass
        sys.stderr.write("\nExiting due to AWS client problem.\n")
        sys.stderr.write("\n\n")
        sys.stderr.flush()
        try:
    	    os.remove("amils_temp.json")
        except:
            pass
        os._exit(1)
