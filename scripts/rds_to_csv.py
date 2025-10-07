#!/usr/bin/env python3
"""
Export AWS RDS DB instances across regions to a CSV.

Examples:
  python scripts/rds_to_csv.py --output rds.csv --include-tags
  python scripts/rds_to_csv.py --regions us-east-1 us-west-2 --output rds.csv
  AWS_PROFILE=prod python scripts/rds_to_csv.py -o rds.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import boto3
from boto3.session import Session
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound


# Canonical CSV columns
CSV_COLUMNS: List[str] = [
    "Region",
    "DBInstanceIdentifier",
    "DBInstanceArn",
    "DBInstanceClass",
    "Engine",
    "EngineVersion",
    "DBInstanceStatus",
    "DBClusterIdentifier",
    "AvailabilityZone",
    "DBSubnetGroupName",
    "VpcId",
    "MultiAZ",
    "PubliclyAccessible",
    "StorageType",
    "AllocatedStorage",
    "Iops",
    "StorageEncrypted",
    "KmsKeyId",
    "EndpointAddress",
    "EndpointPort",
    "EndpointHostedZoneId",
    "PreferredMaintenanceWindow",
    "BackupRetentionPeriod",
    "PreferredBackupWindow",
    "AutoMinorVersionUpgrade",
    "DeletionProtection",
    "IAMDatabaseAuthenticationEnabled",
    "MonitoringInterval",
    "PerformanceInsightsEnabled",
    "PerformanceInsightsKMSKeyId",
    "LicenseModel",
    "InstanceCreateTime",
    "CACertificateIdentifier",
    "CopyTagsToSnapshot",
    "ResourceId",
    "Tags",
]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find RDS DB instances across regions and export details to CSV",
    )
    parser.add_argument(
        "--regions",
        nargs="*",
        help=(
            "Space-separated list of regions to scan. "
            "If omitted, scans all available RDS regions for the account/partition."
        ),
    )
    parser.add_argument(
        "--profile",
        help=(
            "AWS profile name to use. Defaults to environment-configured profile/credentials."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        required=False,
        default="rds_instances.csv",
        help="Output CSV file path (default: rds_instances.csv)",
    )
    parser.add_argument(
        "--include-tags",
        action="store_true",
        help="Include tags for each DB instance (additional API calls per instance).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Max concurrent region workers (default: min(32, number of regions)).",
    )
    return parser.parse_args(argv)


def create_session(profile: Optional[str]) -> Session:
    if profile:
        try:
            return boto3.session.Session(profile_name=profile)
        except ProfileNotFound as exc:
            print(f"Error: AWS profile '{profile}' not found: {exc}", file=sys.stderr)
            sys.exit(2)
    return boto3.session.Session()


def get_regions(session: Session, explicit_regions: Optional[List[str]]) -> List[str]:
    if explicit_regions:
        # Normalize and dedupe while preserving order
        seen = set()
        normalized: List[str] = []
        for region in explicit_regions:
            if not region:
                continue
            r = region.strip()
            if r and r not in seen:
                seen.add(r)
                normalized.append(r)
        return normalized
    # Discover all RDS regions for the account's partition
    regions = session.get_available_regions("rds")
    # Ensure deterministic order
    return sorted(regions)


def safe_datetime_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    # Normalize to ISO8601 without microseconds for compactness
    return dt.replace(microsecond=0).isoformat()


def format_tags(tag_list: Optional[List[Dict[str, str]]]) -> str:
    if not tag_list:
        return ""
    # Use a stable `key=value` pipe-separated list, sorted by key
    try:
        parts = [f"{t.get('Key','')}={t.get('Value','')}" for t in tag_list]
        parts.sort(key=lambda s: s.split("=", 1)[0])
        return " | ".join(parts)
    except Exception:
        # Fallback to string repr if unexpected shape
        return str(tag_list)


def build_row(region: str, inst: Dict[str, Any], tags: Optional[List[Dict[str, str]]]) -> Dict[str, Any]:
    endpoint = inst.get("Endpoint") or {}
    subnet_group = inst.get("DBSubnetGroup") or {}

    row: Dict[str, Any] = {
        "Region": region,
        "DBInstanceIdentifier": inst.get("DBInstanceIdentifier"),
        "DBInstanceArn": inst.get("DBInstanceArn"),
        "DBInstanceClass": inst.get("DBInstanceClass"),
        "Engine": inst.get("Engine"),
        "EngineVersion": inst.get("EngineVersion"),
        "DBInstanceStatus": inst.get("DBInstanceStatus"),
        "DBClusterIdentifier": inst.get("DBClusterIdentifier"),
        "AvailabilityZone": inst.get("AvailabilityZone"),
        "DBSubnetGroupName": subnet_group.get("DBSubnetGroupName"),
        "VpcId": subnet_group.get("VpcId"),
        "MultiAZ": inst.get("MultiAZ"),
        "PubliclyAccessible": inst.get("PubliclyAccessible"),
        "StorageType": inst.get("StorageType"),
        "AllocatedStorage": inst.get("AllocatedStorage"),
        "Iops": inst.get("Iops"),
        "StorageEncrypted": inst.get("StorageEncrypted"),
        "KmsKeyId": inst.get("KmsKeyId"),
        "EndpointAddress": endpoint.get("Address"),
        "EndpointPort": endpoint.get("Port"),
        "EndpointHostedZoneId": endpoint.get("HostedZoneId"),
        "PreferredMaintenanceWindow": inst.get("PreferredMaintenanceWindow"),
        "BackupRetentionPeriod": inst.get("BackupRetentionPeriod"),
        "PreferredBackupWindow": inst.get("PreferredBackupWindow"),
        "AutoMinorVersionUpgrade": inst.get("AutoMinorVersionUpgrade"),
        "DeletionProtection": inst.get("DeletionProtection"),
        "IAMDatabaseAuthenticationEnabled": inst.get("IAMDatabaseAuthenticationEnabled"),
        "MonitoringInterval": inst.get("MonitoringInterval"),
        "PerformanceInsightsEnabled": inst.get("PerformanceInsightsEnabled"),
        "PerformanceInsightsKMSKeyId": inst.get("PerformanceInsightsKMSKeyId"),
        "LicenseModel": inst.get("LicenseModel"),
        "InstanceCreateTime": safe_datetime_iso(inst.get("InstanceCreateTime")),
        "CACertificateIdentifier": inst.get("CACertificateIdentifier"),
        "CopyTagsToSnapshot": inst.get("CopyTagsToSnapshot"),
        "ResourceId": inst.get("DbiResourceId"),
        "Tags": format_tags(tags),
    }
    return row


def fetch_region_instances(
    session: Session, region: str, include_tags: bool
) -> List[Dict[str, Any]]:
    client = session.client("rds", region_name=region)
    paginator = client.get_paginator("describe_db_instances")
    rows: List[Dict[str, Any]] = []

    try:
        for page in paginator.paginate():
            for inst in page.get("DBInstances", []):
                tags = None
                if include_tags:
                    arn = inst.get("DBInstanceArn")
                    if arn:
                        try:
                            tag_resp = client.list_tags_for_resource(ResourceName=arn)
                            tags = tag_resp.get("TagList", [])
                        except ClientError as e:
                            # Ignore tagging errors for this instance, continue
                            print(
                                f"Warning: failed to fetch tags for {arn} in {region}: {e}",
                                file=sys.stderr,
                            )
                rows.append(build_row(region, inst, tags))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # Commonly occurs when region is disabled or opt-in and not enabled
        print(
            f"Warning: skipping region {region} due to error {code}: {e}",
            file=sys.stderr,
        )
    except BotoCoreError as e:
        print(f"Warning: boto core error in region {region}: {e}", file=sys.stderr)

    return rows


def write_csv(output_path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Ensure all values are basic types suitable for CSV
            sanitized = {k: ("" if v is None else v) for k, v in row.items()}
            writer.writerow(sanitized)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    session = create_session(args.profile)

    regions = get_regions(session, args.regions)
    if not regions:
        print("No regions to scan.", file=sys.stderr)
        return 1

    max_workers = args.max_workers or min(32, max(1, len(regions)))

    all_rows: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_region_instances, session, region, args.include_tags): region
            for region in regions
        }
        for future in as_completed(futures):
            region = futures[future]
            try:
                rows = future.result()
                all_rows.extend(rows)
            except Exception as e:  # noqa: BLE001 - last resort to not break the whole run
                print(
                    f"Error: unexpected failure collecting region {region}: {e}",
                    file=sys.stderr,
                )

    write_csv(args.output, all_rows)

    print(f"Wrote {len(all_rows)} RDS DB instances to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
