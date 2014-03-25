Script to launch spot instances in Amazon's EC2 as specified by a launch specification file.
--------------------------------------------------------------------------------------------

Note that this script finishing successfully does not mean the instances
have completed booting (e.g. you may need to wait a bit for sshd to start).
It only means the spot instances successfully launched to an active state
(i.e. the spot request has been fulfilled). Use **-w** to wait for full
initialization.

Requires:

1. A json launch specification file.
2. awscli (pip install awscli).
3. python 2.7 or above.

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
[link](http://docs.aws.amazon.com/cli/latest/reference/ec2/request-spot-instances.html)
for more info.

awscli requires the environment variables AWS\_ACCESS\_KEY\_ID and
AWS\_SECRET\_ACCESS_KEY to be set, or alternatively, the configuration
file ~/.aws/config on Linux/OSX or C:\Users\USER NAME\\.aws\config on Windows.
A minimal example of this file is:

    [default]
    aws_access_key_id = YOUR_ACCESS_KEY
    aws_secret_access_key = YOUR_SECRET_KEY

USER\_DATA\_FILE\_NAME is optional and specifies a file containing user data to be made
available to all launched AMIs. Note that if the user data begins with an interpreter
(e.g. #!/bin/bash), an Amazon supplied AMI will regard the user data as a bootstrap
script and automatically run it (as root) at AMI boot time.

EC2 launch region is derived from:

1. The AWS\_DEFAULT\_REGION environment variable.
2. Adding a line like 'region = us-east-1' to ~/.aws/config under the [default] section. 
3. Placement:AvailabilityZone specified in launch spec file as in the above example; if
   AvailabilityZone is set to something like "us-east-1" instead of a proper "us-east-1c", 
   then the placement will be in the region us-east-1 and the actual zone will be
   determined by Amazon. In both cases, the region is derived from the AvailabilityZone
   value and overrides any region set by method 1 or 2. Setting a proper AvailabilityZone
   may be desirable when best historical spot price zone is a concern.

Output is instance ID pubilc IP pairs to stdout (use **--ppip** for private IPs instead).


pdanford@pdanford.com - Feb, 2014
