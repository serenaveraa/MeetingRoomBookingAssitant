#!/usr/bin/env python3
"""SUPERSEDED — use CloudFormation instead.

This boto3 script only created RDS. Prefer:

  BootstrapMode=true ./infra/scripts/deploy.sh
  ./infra/scripts/build_and_push.sh
  ./infra/scripts/deploy.sh
  python infra/migrate_rds.py

See infra/README.md and infra/cloudformation/odc-stack.yaml.

---
Legacy: Provision Free Tier RDS Postgres for the ODC booking app (issue #43).

Requires AWS credentials with EC2 + RDS permissions:
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  export AWS_DEFAULT_REGION=us-east-2

Usage:
  cd backend && . .venv/bin/activate
  python ../infra/provision_rds.py
"""

from __future__ import annotations

import json
import secrets
import string
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-2"
DB_ID = "odc-meeting-room"
DB_NAME = "meeting_room"
DB_USER = "odcadmin"
OUT_DIR = Path(__file__).resolve().parent
LOCAL_ENV = OUT_DIR / "local.env"
OUTPUTS = OUT_DIR / "outputs.json"


def _password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _default_vpc(ec2):
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SystemExit("No default VPC in this region. Create one or pick another region.")
    return vpcs[0]["VpcId"]


def _subnets(ec2, vpc_id: str) -> list[str]:
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    # Prefer distinct AZs for subnet group (RDS still single-AZ instance).
    by_az: dict[str, str] = {}
    for s in sorted(subs, key=lambda x: x["AvailabilityZone"]):
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    ids = list(by_az.values())
    if len(ids) < 2:
        raise SystemExit("Need at least 2 subnets in different AZs for DBSubnetGroup.")
    return ids[:2]


def _ensure_sg(ec2, vpc_id: str, name: str, description: str, ingress: list[dict]) -> str:
    existing = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if existing:
        return existing[0]["GroupId"]
    sg = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)
    sg_id = sg["GroupId"]
    if ingress:
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=ingress)
    ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "Name", "Value": name}])
    return sg_id


def main() -> int:
    session = boto3.Session(region_name=REGION)
    sts = session.client("sts")
    try:
        ident = sts.get_caller_identity()
    except ClientError as exc:
        print("AWS credentials missing or invalid.", file=sys.stderr)
        print(
            "Create an IAM access key for user CLI, then:\n"
            "  export AWS_ACCESS_KEY_ID=...\n"
            "  export AWS_SECRET_ACCESS_KEY=...\n"
            "  export AWS_DEFAULT_REGION=us-east-2",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        return 1

    print(f"Account={ident['Account']} Arn={ident['Arn']} Region={REGION}")

    ec2 = session.client("ec2")
    rds = session.client("rds")
    vpc_id = _default_vpc(ec2)
    subnet_ids = _subnets(ec2, vpc_id)
    print(f"VPC={vpc_id} Subnets={subnet_ids}")

    rds_sg = _ensure_sg(
        ec2,
        vpc_id,
        "odc-meeting-rds-sg",
        "ODC meeting room Postgres (Free Tier demo)",
        [
            {
                "IpProtocol": "tcp",
                "FromPort": 5432,
                "ToPort": 5432,
                "IpRanges": [
                    {
                        "CidrIp": "0.0.0.0/0",
                        "Description": "Public Lambda demo access - tighten later",
                    }
                ],
            }
        ],
    )
    print(f"RDS SG={rds_sg}")

    # Subnet group
    try:
        rds.create_db_subnet_group(
            DBSubnetGroupName="odc-meeting-subnet-group",
            DBSubnetGroupDescription="ODC meeting room Free Tier",
            SubnetIds=subnet_ids,
            Tags=[{"Key": "Name", "Value": "odc-meeting-subnet-group"}],
        )
        print("Created DB subnet group")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "DBSubnetGroupAlreadyExists":
            raise
        print("DB subnet group already exists")

    password = _password()
    try:
        rds.describe_db_instances(DBInstanceIdentifier=DB_ID)
        print(f"RDS instance {DB_ID} already exists — not recreating password.")
        # Keep existing local.env if present
        if not LOCAL_ENV.exists():
            print(
                f"WARNING: {LOCAL_ENV} missing; set DATABASE_URL manually from console.",
                file=sys.stderr,
            )
        else:
            print(f"Reusing credentials in {LOCAL_ENV}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "DBInstanceNotFound":
            raise
        print(f"Creating RDS {DB_ID} (db.t3.micro, postgres 16)…")
        rds.create_db_instance(
            DBInstanceIdentifier=DB_ID,
            DBName=DB_NAME,
            Engine="postgres",
            EngineVersion="16.14",
            DBInstanceClass="db.t3.micro",
            AllocatedStorage=20,
            StorageType="gp2",
            MasterUsername=DB_USER,
            MasterUserPassword=password,
            VpcSecurityGroupIds=[rds_sg],
            DBSubnetGroupName="odc-meeting-subnet-group",
            PubliclyAccessible=True,
            MultiAZ=False,
            BackupRetentionPeriod=1,
            AutoMinorVersionUpgrade=True,
            Tags=[{"Key": "Name", "Value": DB_ID}, {"Key": "Project", "Value": "odc-meeting"}],
        )
        LOCAL_ENV.write_text(
            "\n".join(
                [
                    f"AWS_DEFAULT_REGION={REGION}",
                    f"DB_INSTANCE_ID={DB_ID}",
                    f"DB_USER={DB_USER}",
                    f"DB_PASSWORD={password}",
                    f"DB_NAME={DB_NAME}",
                    "",
                ]
            )
            + "\n"
        )
        LOCAL_ENV.chmod(0o600)
        print(f"Wrote {LOCAL_ENV}")

    print("Waiting for RDS available (often 5–15 minutes)…")
    waiter = rds.get_waiter("db_instance_available")
    waiter.wait(DBInstanceIdentifier=DB_ID)
    inst = rds.describe_db_instances(DBInstanceIdentifier=DB_ID)["DBInstances"][0]
    endpoint = inst["Endpoint"]["Address"]
    port = inst["Endpoint"]["Port"]

    # Load password from local.env if we did not just create it
    env_vars = {}
    if LOCAL_ENV.exists():
        for line in LOCAL_ENV.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k] = v
    db_user = env_vars.get("DB_USER", DB_USER)
    db_password = env_vars.get("DB_PASSWORD", "")
    if not db_password:
        raise SystemExit(f"Missing DB_PASSWORD in {LOCAL_ENV}")

    database_url = (
        f"postgresql+psycopg://{db_user}:{db_password}@{endpoint}:{port}/{DB_NAME}"
        f"?sslmode=require"
    )
    # Append DATABASE_URL to local.env
    lines = LOCAL_ENV.read_text().splitlines() if LOCAL_ENV.exists() else []
    lines = [ln for ln in lines if not ln.startswith("DATABASE_URL=") and not ln.startswith("DB_ENDPOINT=")]
    lines.extend([f"DB_ENDPOINT={endpoint}", f"DATABASE_URL={database_url}", ""])
    LOCAL_ENV.write_text("\n".join(lines))
    LOCAL_ENV.chmod(0o600)

    outputs = {
        "region": REGION,
        "account": ident["Account"],
        "db_instance_id": DB_ID,
        "endpoint": endpoint,
        "port": port,
        "database": DB_NAME,
        "username": db_user,
        "security_group_id": rds_sg,
        "database_url_env_file": str(LOCAL_ENV),
    }
    OUTPUTS.write_text(json.dumps(outputs, indent=2) + "\n")
    print(json.dumps(outputs, indent=2))
    print(f"\nDATABASE_URL saved in {LOCAL_ENV} (gitignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
