import datetime
import json
import unittest
import os

import httpx
import jmespath
import moto
import boto3
import uvicorn

from fastapi.testclient import TestClient


@moto.mock_secretsmanager
@moto.mock_route53
class TestDynR53(unittest.TestCase):
    ip = '1.1.1.1'
    domain_name = 'example.com'
    hostname = f'www.{domain_name}'

    def setUp(self) -> None:
        sm_client = boto3.client('secretsmanager')
        self.r53_client = boto3.client('route53')

        self.password = 'secret12345'

        secret = {
            'username': 'admin',
            'password': self.password
        }

        sm_client.create_secret(
            Name='dynr53/users/admin',
            SecretString=json.dumps(secret),
        )

        create_hosted_zone_response = self.r53_client.create_hosted_zone(
            Name=self.domain_name,
            CallerReference=str(datetime.datetime.utcnow().timestamp())
        )
        self.hosted_zone_id = create_hosted_zone_response['HostedZone']['Id']

        os.environ.update(
            {
             'POWERTOOLS_SERVICE_NAME': 'dynr53',
             # 'POWERTOOLS_LOG_LEVEL': 'DEBUG'
            }
        )

    def test_no_auth(self):
        import index

        client = TestClient(index.app)

        response = client.get("/nic/update")
        self.assertEqual(response.status_code, 401)

    def test_bad_user(self):
        import index

        client = TestClient(index.app)
        basic_auth = httpx.BasicAuth('wrong', self.password)
        response = client.get("/nic/update", auth=basic_auth)
        self.assertEqual(response.status_code, 401)

    def test_bad_password(self):
        import index

        client = TestClient(index.app)
        basic_auth = httpx.BasicAuth('admin', 'wrong')
        response = client.get("/nic/update", auth=basic_auth)
        self.assertEqual(response.status_code, 401)

    def test_happy_path_explicit_ip(self):
        import index

        client = TestClient(index.app)
        basic_auth = httpx.BasicAuth('admin', self.password)
        response = client.get(f"/nic/update?hostname={self.hostname}&myip={self.ip}", auth=basic_auth)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(e.response.json())
            raise

        self.assertEqual(f'good {self.ip}', response.text)

        list_resource_record_sets_paginator = self.r53_client.get_paginator('list_resource_record_sets')
        list_resource_record_sets_paginator_iterator = list_resource_record_sets_paginator.paginate(
            HostedZoneId=self.hosted_zone_id
        )

        record_set = list(
            list_resource_record_sets_paginator_iterator.search(
                f"ResourceRecordSets[?Name == '{self.hostname}.']"
            )
        )[0]

        self.assertEqual(record_set['Type'], 'A')
        self.assertEqual(record_set['TTL'], 60)

        ip_record_value = jmespath.search(f"ResourceRecords[?Value == '{self.ip}'].Value", record_set)[0]
        self.assertEqual(self.ip, ip_record_value)

    def test_happy_path_explicit_ip_existing_record(self):
        import index

        self.r53_client.change_resource_record_sets(
            HostedZoneId=self.hosted_zone_id,
            ChangeBatch={
                'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': self.hostname,
                            'Type': 'A',
                            'TTL': 60,
                            'ResourceRecords': [
                                {
                                    'Value': str(self.ip)
                                }
                            ]
                        }
                    },
                ]
            }
        )

        client = TestClient(index.app)
        basic_auth = httpx.BasicAuth('admin', self.password)
        response = client.get(f"/nic/update?hostname={self.hostname}&myip={self.ip}", auth=basic_auth)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(e.response.json())
            raise

        self.assertEqual(f'nochg {self.ip}', response.text)

        list_resource_record_sets_paginator = self.r53_client.get_paginator('list_resource_record_sets')
        list_resource_record_sets_paginator_iterator = list_resource_record_sets_paginator.paginate(
            HostedZoneId=self.hosted_zone_id
        )

        record_set = list(
            list_resource_record_sets_paginator_iterator.search(
                f"ResourceRecordSets[?Name == '{self.hostname}.']"
            )
        )[0]

        self.assertEqual(record_set['Type'], 'A')
        self.assertEqual(record_set['TTL'], 60)

        ip_record_value = jmespath.search(f"ResourceRecords[?Value == '{self.ip}'].Value", record_set)[0]
        self.assertEqual(self.ip, ip_record_value)

    @unittest.skip
    def test_local(self):
        os.environ['public_docs'] = 'true'
        import index

        uvicorn.run(app=index.app)


if __name__ == '__main__':
    unittest.main()
