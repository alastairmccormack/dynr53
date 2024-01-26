# Dynr53 - Dyndns2 for Route53

Packaged in a simple to use CDK stack, Dynr53 is a lightweight AWS Lambda script to update Route53 using the Dyndns2
"spec", as supported by ddclient and used in many routers such as OpenWRT and Ubiquiti USG + UDMs.

## Configuration

### Allowed Zones / Domains
For simplicity and security, the zones that Dynr53 is permitted to modify is configured at deploy time through the use
of IAM permissions. 

This configuration is controlled via the `dynr53/allowed_zones` context in `cdk.context.json`:

```json
{
  "dynr53/allowed_zones": {
    "example.com": {
      "allowed_records": [
        "vpn"
      ]
    }
  }
}
```
In this example, Dynr53 is allowed to modify the `vpn.example.com` record.

Change `example.com` to your own zone name. In `allowed_records`, set the unqualified record name you wish to allow
Dynr53 to modify.

## Installation

```commandline
cdk deploy
```

The Lambda URL is printed along with a link to help you find the auto generated password in AWS Secrets Manager.
<br>The username is always `admin`.

## OpenAPI / Swagger Docs

The OpenAPI docs are available at `/docs`. By default, you'll need provide Basic Authentication credentials to access
them. To make the docs public, set `PUBLIC_DOCS=true` in the Lambda environment variables.