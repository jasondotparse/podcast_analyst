import { test } from 'node:test';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { PodcastAnalystStack } from '../lib/podcast-analyst-stack';

function template(stage: 'beta' | 'prod' = 'beta'): Template {
  const stack = new PodcastAnalystStack(new App({ context: {
    '@aws-cdk/aws-ec2:uniqueImdsv2TemplateName': true,
    'ssm:account=111111111111:parameterName=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64:region=us-east-1': 'ami-0123456789abcdef0',
  } }), `Test-${stage}`, {
    env: { account: '111111111111', region: 'us-east-1' },
    stage,
    domainName: 'example.com',
  });
  return Template.fromStack(stack);
}

test('one host, no NAT or load balancer, and only HTTP/HTTPS inbound', () => {
  const result = template();
  result.resourceCountIs('AWS::EC2::Instance', 1);
  result.resourceCountIs('AWS::EC2::NatGateway', 0);
  result.resourceCountIs('AWS::ElasticLoadBalancingV2::LoadBalancer', 0);
  result.hasResourceProperties('AWS::EC2::SecurityGroup', {
    SecurityGroupIngress: [
      Match.objectLike({ CidrIp: '0.0.0.0/0', FromPort: 80, ToPort: 80, IpProtocol: 'tcp' }),
      Match.objectLike({ CidrIp: '0.0.0.0/0', FromPort: 443, ToPort: 443, IpProtocol: 'tcp' }),
    ],
  });
  result.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 't3.medium',
    CreditSpecification: { CPUCredits: 'unlimited' },
    KeyName: Match.absent(),
    BlockDeviceMappings: [Match.objectLike({ Ebs: Match.objectLike({ Encrypted: true }) })],
  });
  result.hasResourceProperties('AWS::EC2::LaunchTemplate', {
    LaunchTemplateData: { MetadataOptions: { HttpTokens: 'required' } },
  });
});

test('releases have immutable tags and survive stack deletion', () => {
  template().hasResource('AWS::ECR::Repository', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: Match.objectLike({ ImageTagMutability: 'IMMUTABLE' }),
  });
});

test('beta and prod use separate hostnames', () => {
  template('beta').hasResourceProperties('AWS::Route53::RecordSet', { Name: 'beta.example.com.' });
  template('prod').hasResourceProperties('AWS::Route53::RecordSet', { Name: 'example.com.' });
});
