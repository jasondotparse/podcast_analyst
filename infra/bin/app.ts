#!/usr/bin/env node
import { App, Tags } from 'aws-cdk-lib';
import { PodcastAnalystStack } from '../lib/podcast-analyst-stack';

const app = new App();
const stage = app.node.tryGetContext('stage');
if (stage !== 'beta' && stage !== 'prod') {
  throw new Error('Use -c stage=beta or -c stage=prod');
}

new PodcastAnalystStack(app, `PodcastAnalyst-${stage}`, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
  stage,
  domainName: app.node.tryGetContext('domainName'),
});
Tags.of(app).add('Project', 'podcast-analyst');
Tags.of(app).add('Environment', stage);
