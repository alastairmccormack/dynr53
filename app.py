#!/usr/bin/env python3
import os

import aws_cdk as cdk

from dynr53.dynr53_stack import Dynr53Stack


app = cdk.App()
Dynr53Stack(
    app,
    "Dynr53Stack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION")
    )
)

app.synth()
