import functools
import json
import logging
import secrets
from typing import Annotated, Optional

import boto3
import jmespath
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import PlainTextResponse
from mangum import Mangum
from pydantic.networks import IPvAnyAddress
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit  # noqa: F401
from starlette.requests import Request
from starlette import status
from aws_lambda_powertools.utilities import parameters
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    public_docs: Optional[bool] = False


settings = Settings()

app = FastAPI(
    title='Dynr53',
    default_response_class=PlainTextResponse,
    docs_url=None if not settings.public_docs else '/docs',
    redoc_url=None if not settings.public_docs else '/redoc',
)

security = HTTPBasic()

logger: Logger = Logger()
metrics: Metrics = Metrics()
tracer: Tracer = Tracer()


@functools.lru_cache()
def get_admin_password() -> str:
    # In a cachable method to allow for offline docs
    admin_user_secret = json.loads(parameters.get_secret('dynr53/users/admin'))
    return admin_user_secret['password']


@functools.lru_cache()
def get_r53_client():
    # In a cachable method to allow for offline docs
    return boto3.client('route53')


def validate_credentials(credentials: HTTPBasicCredentials = Depends(security)):

    username_checks = secrets.compare_digest(b'admin', credentials.username.encode('utf-8'))
    password_checks = secrets.compare_digest(
        get_admin_password().encode('utf-8'), credentials.password.encode('utf-8')
    )

    if not (username_checks and password_checks):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="badauth",
            headers={"WWW-Authenticate": "Basic"}
        )


def get_ip_from_headers(request: Request) -> IPvAnyAddress:
    try:
        return IPvAnyAddress(request.headers['x-forwarded-for'])
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='IP can not be determined from x-forward-for header'
        )


def get_hosted_zone_id_from_fqdn(hostname: str) -> str:
    # TODO: support apex records and subdomains
    zone_name = hostname.split('.', 1)[-1]
    logger.debug(f'Zone name for "{hostname}": "{zone_name}"')
    get_r53_client().list_hosted_zones_by_name(DNSName=zone_name)

    list_hosted_zones_paginator = get_r53_client().get_paginator('list_hosted_zones')

    response_iterator = list_hosted_zones_paginator.paginate()
    list_hosted_zones_filtered_iterator = response_iterator.search(f"HostedZones[?Name == '{zone_name}.'][Id]")

    try:
        zone_id = list(list_hosted_zones_filtered_iterator)[0][0]
        logger.debug(f'Zone id for "{zone_name}" = {zone_id}')
        return zone_id
    except IndexError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Hosted zone {zone_name} not found'
        )


def update_r53_record(hostname: str, hosted_zone_id: str, ip: IPvAnyAddress):
    # TODO: Support IPv6
    dns_ttl = 60

    logger.info(f'Upserting A record for {hostname} = {ip}, ttl = {dns_ttl}')

    get_r53_client().change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={
            'Changes': [
                {
                    'Action': 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': hostname,
                        'Type': 'A',
                        'TTL': dns_ttl,
                        'ResourceRecords': [
                            {
                                'Value': str(ip)
                            },
                        ],
                    }
                },
            ]
        }
    )


def is_existing_record_exists(hostname: str, hosted_zone_id: str, ip: IPvAnyAddress) -> bool:
    list_resource_record_sets_response = get_r53_client().list_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        StartRecordName=hostname,
        StartRecordType='A'
    )

    existing_match = jmespath.search(
        f"ResourceRecordSets[?Name == '{hostname}.'][ResourceRecords][][?Value == '{ip}'][]",
        list_resource_record_sets_response
    )

    if existing_match:
        logger.info(f'{hostname} already points to {ip}')
        return True
    else:
        return False


@app.get(
    path='/nic/update',
    responses={
        401: {
            'content': {
                'text/plain': {
                    'example': 'badauth <ip>'
                }
            }
        },
        200: {
            'content': {
                'text/plain': {
                    'examples': {
                        'Record updated': {
                            'value': 'good <myip>'
                        },
                        'No Changes': {
                            'value': 'nochg <myip>'
                        }
                     }
                }
            }
        }
    }
)
def update(
        request: Request,
        hostname: str,
        _: Annotated[HTTPBasicCredentials, Depends(validate_credentials)],
        myip: Optional[IPvAnyAddress] = None,
):

    if not myip:
        myip = get_ip_from_headers(request=request)

    hosted_zone_id = get_hosted_zone_id_from_fqdn(hostname=hostname)

    ere = is_existing_record_exists(hostname=hostname, hosted_zone_id=hosted_zone_id, ip=myip)
    if ere:
        message = f'nochg {myip}'
        logger.info('No record change required')
    else:
        update_r53_record(hostname=hostname, hosted_zone_id=hosted_zone_id, ip=myip)
        message = f'good {myip}'

    return PlainTextResponse(message)


if not settings.public_docs:
    # Hide docs behind authentication: https://github.com/tiangolo/fastapi/issues/364#issuecomment-890853577
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
    from fastapi.openapi.utils import get_openapi

    @app.get("/docs", include_in_schema=False)
    async def get_swagger_documentation(credentials: Annotated[HTTPBasicCredentials, Depends(validate_credentials)]):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")

    @app.get("/redoc", include_in_schema=False)
    async def get_redoc_documentation(credentials: Annotated[HTTPBasicCredentials, Depends(validate_credentials)]):
        return get_redoc_html(openapi_url="/openapi.json", title="docs")

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi(credentials: Annotated[HTTPBasicCredentials, Depends(validate_credentials)]):
        return get_openapi(title=app.title, version=app.version, routes=app.routes)

handler = Mangum(app)
handler = logger.inject_lambda_context(handler, clear_state=True, log_event=(logger.log_level == logging.DEBUG))

