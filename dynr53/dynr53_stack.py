import json
import typing
import urllib.parse

import aws_cdk
import aws_cdk.aws_iam
import aws_cdk.aws_route53
import aws_cdk.aws_secretsmanager
import aws_cdk.aws_lambda
from constructs import Construct
import cloudsnorkel.cdk_turbo_layers


class Dynr53Stack(aws_cdk.Stack):
    default_user = 'admin'

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        python_function = self.lambda_function()
        self.zone_permissions(python_function=python_function)
        self.secrets(python_function=python_function)

    def lambda_function(self) -> aws_cdk.aws_lambda.Function:
        # Uses the awesome Turbo Layers Construct to package + compile dependancies in a Lambda
        # No more waiting for Docker!
        deps_packager = cloudsnorkel.cdk_turbo_layers.PythonDependencyPackager(
            scope=self,
            id='deps_layer',
            architecture=aws_cdk.aws_lambda.Architecture.ARM_64,
            runtime=aws_cdk.aws_lambda.Runtime.PYTHON_3_9,
            type=cloudsnorkel.cdk_turbo_layers.DependencyPackagerType.LAMBDA
        )

        python_function = aws_cdk.aws_lambda.Function(
            scope=self,
            id='dynr53-lambda',
            function_name='dynr53',
            code=aws_cdk.aws_lambda.AssetCode.from_asset(
                path='lambda',
            ),
            handler='index.handler',
            runtime=aws_cdk.aws_lambda.Runtime.PYTHON_3_9,
            timeout=aws_cdk.Duration.minutes(1),
            memory_size=256,
            log_retention=aws_cdk.aws_logs.RetentionDays.THREE_MONTHS,
            architecture=aws_cdk.aws_lambda.Architecture.ARM_64,
            layers=[
                deps_packager.layer_from_pipenv(
                    id='pipenv_layer',
                    path='.'
                )
            ],
            environment={
                'POWERTOOLS_SERVICE_NAME': 'dynr53',
                'PUBLIC_DOCS': 'false'
            },
        )

        function_url = python_function.add_function_url(
            auth_type=aws_cdk.aws_lambda.FunctionUrlAuthType.NONE,
        )

        aws_cdk.CfnOutput(
            scope=self,
            id='function-url',
            export_name='functionUrl',
            key='functionUrl',
            value=f'{function_url.url}nic/update'
        )

        return python_function

    def secrets(self, python_function: aws_cdk.aws_lambda.Function):
        secret_name = f'dynr53/users/{self.default_user}'

        admin_user_secret = aws_cdk.aws_secretsmanager.Secret(
            scope=self,
            id='secrets',
            secret_name=secret_name,
            generate_secret_string=aws_cdk.aws_secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({'username': self.default_user}),
                generate_string_key='password',
                exclude_punctuation=True,
                exclude_characters='@'
            )
        )

        # A quick link to get the admin secret
        aws_cdk.CfnOutput(
            scope=self,
            id='secrets-console-url',
            export_name='secretsConsoleUrl',
            value=f'https://{self.region}.console.aws.amazon.com/secretsmanager/'
                  f'secret?name={urllib.parse.quote(secret_name)}'
        )

        admin_user_secret.grant_read(python_function)

    def zone_permissions(self, python_function: aws_cdk.aws_lambda.Function):
        allowed_zones_config: typing.Dict = self.node.get_context('dynr53/allowed_zones')

        for zone_name, zone_config in allowed_zones_config.items():

            fq_records = [f'{x}.{zone_name}' for x in zone_config['allowed_records']]

            zone = aws_cdk.aws_route53.HostedZone.from_lookup(
                scope=self,
                id=f'{zone_name}-zone',
                domain_name=zone_name
            )

            python_function.add_to_role_policy(
                statement=aws_cdk.aws_iam.PolicyStatement(
                    actions=[
                        'route53:ChangeResourceRecordSets',
                        'route53:ListResourceRecordSets',
                    ],
                    resources=[
                        zone.hosted_zone_arn
                    ],
                    conditions={
                        'ForAllValues:StringLike': {
                            'route53:ChangeResourceRecordSetsNormalizedRecordNames': fq_records
                        }
                    }
                )
            )

            python_function.add_to_role_policy(
                statement=aws_cdk.aws_iam.PolicyStatement(
                    actions=[
                        'route53:ListHostedZonesByName',
                        'route53:ListHostedZones',
                    ],
                    resources=[
                        '*'
                    ],
                )
            )

