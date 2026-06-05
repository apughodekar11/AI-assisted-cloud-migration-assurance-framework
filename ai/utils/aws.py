import boto3

def cw_client(region: str):
    return boto3.client("cloudwatch", region_name=region)

def ce_client(region: str = "us-east-1"):
    return boto3.client("ce", region_name=region)

