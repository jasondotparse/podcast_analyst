import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  CfnOutput, RemovalPolicy, Stack, StackProps,
  aws_ec2 as ec2, aws_ecr as ecr, aws_iam as iam, aws_route53 as route53,
  aws_ses as ses,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface PodcastAnalystStackProps extends StackProps {
  stage: 'alpha' | 'beta' | 'prod';
  domainName: string;
}

export class PodcastAnalystStack extends Stack {
  constructor(scope: Construct, id: string, props: PodcastAnalystStackProps) {
    super(scope, id, props);

    const hostname = props.stage === 'prod'
      ? props.domainName : `${props.stage}.${props.domainName}`;
    const zone = route53.HostedZone.fromLookup(this, 'Zone', {
      domainName: props.domainName,
      privateZone: false,
    });
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 1,
      natGateways: 0,
      subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC }],
    });
    const securityGroup = new ec2.SecurityGroup(this, 'WebAccess', { vpc });
    securityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'HTTP redirect / ACME');
    securityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'HTTPS');

    const repository = new ecr.Repository(this, 'AppImages', {
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      imageScanOnPush: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const role = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')],
    });
    repository.grantPull(role);
    const emailIdentity = new ses.EmailIdentity(this, 'AlertEmailIdentity', {
      identity: ses.Identity.domain(hostname),
    });
    emailIdentity.dkimRecords.forEach((record, index) => {
      new route53.CnameRecord(this, `AlertDkim${index}`, {
        // SES returns a full hostname token; the trailing dot prevents CDK appending the zone again.
        zone, recordName: `${record.name}.`, domainName: record.value,
      });
    });
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ses:SendEmail'],
      resources: [emailIdentity.emailIdentityArn],
    }));

    const instance = new ec2.Instance(this, 'AppHost', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroup,
      role,
      instanceType: new ec2.InstanceType('t3.medium'),
      creditSpecification: ec2.CpuCredits.UNLIMITED,
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.X86_64,
        cachedInContext: true,
      }),
      requireImdsv2: true,
      userDataCausesReplacement: true,
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(props.stage === 'prod' ? 32 : 16, {
          volumeType: ec2.EbsDeviceVolumeType.GP3,
          encrypted: true,
          deleteOnTermination: true,
        }),
      }],
    });
    instance.addUserData(readFileSync(join(__dirname, '../..', 'bootstrap.sh'), 'utf8'));
    const address = new ec2.CfnEIP(this, 'Address', { domain: 'vpc' });
    new ec2.CfnEIPAssociation(this, 'AddressAssociation', {
      allocationId: address.attrAllocationId,
      instanceId: instance.instanceId,
    });
    new route53.ARecord(this, 'AppDns', {
      zone,
      recordName: hostname,
      target: route53.RecordTarget.fromIpAddresses(address.ref),
    });

    new CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new CfnOutput(this, 'RepositoryUri', { value: repository.repositoryUri });
    new CfnOutput(this, 'AppUrl', { value: `https://${hostname}` });
    new CfnOutput(this, 'PublicIp', { value: address.ref });
  }
}
