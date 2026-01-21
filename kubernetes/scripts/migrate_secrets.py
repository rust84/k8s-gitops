#!/usr/bin/env python3
"""
Migrate secrets from Doppler to 1Password.

This script migrates all Doppler projects to 1Password Secure Notes in the Kubernetes vault.
It handles multi-config projects, filters metadata keys, and validates successful creation.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass
class MigrationResult:
    """Result of migrating a single project."""
    project: str
    status: str  # 'success', 'skipped', 'failed'
    key_count: int
    config_count: int
    error: Optional[str] = None


class SecretMigrator:
    """Handles migration of secrets from Doppler to 1Password."""

    VAULT_NAME = "Kubernetes"
    SKIP_KEYS = {"DOPPLER_CONFIG", "DOPPLER_ENVIRONMENT", "DOPPLER_PROJECT"}

    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self.dry_run = dry_run
        self.verbose = verbose

    def log(self, message: str, force: bool = False):
        """Log message if verbose or force is True."""
        if self.verbose or force:
            print(message)

    def run_command(self, cmd: List[str], capture_output: bool = True) -> Optional[str]:
        """Run shell command and return output."""
        try:
            if self.verbose:
                self.log(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                check=True
            )
            return result.stdout if capture_output else None
        except subprocess.CalledProcessError as e:
            if self.verbose:
                self.log(f"Command failed: {e.stderr}")
            return None
        except FileNotFoundError:
            self.log(f"Command not found: {cmd[0]}", force=True)
            return None

    def get_doppler_projects(self) -> List[str]:
        """Get list of all Doppler projects."""
        self.log("Fetching Doppler projects...")
        output = self.run_command(["doppler", "projects", "--json"])

        if not output:
            return []

        try:
            projects_data = json.loads(output)
            projects = [p["name"] for p in projects_data]
            self.log(f"Found {len(projects)} Doppler projects")
            return projects
        except (json.JSONDecodeError, KeyError) as e:
            self.log(f"Failed to parse Doppler projects: {e}", force=True)
            return []

    def get_doppler_configs(self, project: str) -> List[str]:
        """Get configs for a Doppler project."""
        self.log(f"Fetching configs for project '{project}'...")
        output = self.run_command([
            "doppler", "configs", "--project", project, "--json"
        ])

        if not output:
            return []

        try:
            configs_data = json.loads(output)
            configs = [c["name"] for c in configs_data]
            self.log(f"Found {len(configs)} configs: {', '.join(configs)}")
            return configs
        except (json.JSONDecodeError, KeyError) as e:
            self.log(f"Failed to parse configs for {project}: {e}", force=True)
            return []

    def extract_doppler_secrets(self, project: str, config: str) -> Dict[str, str]:
        """Extract secrets from Doppler project/config."""
        self.log(f"Extracting secrets from {project}/{config}...")
        output = self.run_command([
            "doppler", "secrets", "--project", project, "--config", config,
            "--json"
        ])

        if not output:
            return {}

        try:
            secrets_data = json.loads(output)
            # Extract computed values and filter out metadata keys
            secrets = {
                key: value["computed"]
                for key, value in secrets_data.items()
                if key not in self.SKIP_KEYS
            }
            self.log(f"Extracted {len(secrets)} secrets (filtered {len(self.SKIP_KEYS)} metadata keys)")
            return secrets
        except (json.JSONDecodeError, KeyError) as e:
            self.log(f"Failed to parse secrets for {project}/{config}: {e}", force=True)
            return {}

    def onepassword_item_exists(self, name: str) -> bool:
        """Check if 1Password item exists."""
        self.log(f"Checking if 1Password item '{name}' exists...")
        output = self.run_command([
            "op", "item", "get", name, "--vault", self.VAULT_NAME, "--format", "json"
        ])

        exists = output is not None
        self.log(f"Item '{name}' {'exists' if exists else 'does not exist'}")
        return exists

    def merge_configs_secrets(self, configs_secrets: List[Dict[str, str]]) -> Dict[str, str]:
        """Merge secrets from multiple configs."""
        self.log("Merging secrets from multiple configs...")
        merged = {}
        duplicates = {}

        for secrets in configs_secrets:
            for key, value in secrets.items():
                if key in merged:
                    # Track duplicates (like POSTGRES_SUPER_PASS)
                    if key not in duplicates:
                        duplicates[key] = {merged[key]}
                    duplicates[key].add(value)
                else:
                    merged[key] = value

        # Log duplicate keys and their values
        for key, values in duplicates.items():
            if len(values) > 1:
                self.log(f"WARNING: Key '{key}' has different values across configs: {values}", force=True)
            else:
                self.log(f"Key '{key}' duplicated with same value (keeping one copy)")

        self.log(f"Merged to {len(merged)} unique keys")
        return merged

    def create_onepassword_item(self, name: str, secrets: Dict[str, str]) -> bool:
        """Create 1Password Secure Note with secrets."""
        self.log(f"Creating 1Password item '{name}' with {len(secrets)} secrets...")

        if self.dry_run:
            self.log(f"DRY RUN: Would create item '{name}' with {len(secrets)} secrets", force=True)
            # Show first 3 keys as preview
            preview_keys = list(secrets.keys())[:3]
            for key in preview_keys:
                self.log(f"  - {key.lower()}: ***", force=True)
            if len(secrets) > 3:
                self.log(f"  ... and {len(secrets) - 3} more", force=True)
            return True

        # Build command with all secrets as concealed fields
        cmd = [
            "op", "item", "create",
            "--vault", self.VAULT_NAME,
            "--category", "Secure Note",
            "--title", name
        ]

        # Add each secret as a concealed field in the "add more" section
        for key, value in secrets.items():
            # Use lowercase key to match existing pattern (karakeep uses lowercase)
            field_spec = f"add more.{key.lower()}[concealed]={value}"
            cmd.append(field_spec)

        output = self.run_command(cmd)

        if output:
            self.log(f"Successfully created item '{name}'", force=True)
            return True
        else:
            self.log(f"Failed to create item '{name}'", force=True)
            return False

    def validate_onepassword_item(self, name: str, expected_keys: Set[str]) -> tuple[bool, Set[str]]:
        """Validate all keys exist in created item."""
        self.log(f"Validating 1Password item '{name}'...")

        if self.dry_run:
            self.log(f"DRY RUN: Skipping validation", force=True)
            return True, set()

        output = self.run_command([
            "op", "item", "get", name, "--vault", self.VAULT_NAME, "--format", "json"
        ])

        if not output:
            self.log(f"Failed to fetch item '{name}' for validation", force=True)
            return False, expected_keys

        try:
            item_data = json.loads(output)
            # Extract field labels from the item
            existing_keys = set()
            for field in item_data.get("fields", []):
                label = field.get("label", "")
                if label:
                    # Convert to uppercase to match expected_keys format
                    existing_keys.add(label.upper())

            # Compare expected vs existing
            expected_upper = {k.upper() for k in expected_keys}
            missing_keys = expected_upper - existing_keys

            if missing_keys:
                self.log(f"WARNING: Missing {len(missing_keys)} keys in '{name}': {missing_keys}", force=True)
                return False, missing_keys
            else:
                self.log(f"All {len(expected_keys)} keys validated successfully")
                return True, set()
        except (json.JSONDecodeError, KeyError) as e:
            self.log(f"Failed to parse item data for validation: {e}", force=True)
            return False, expected_keys

    def migrate_project(self, project: str) -> MigrationResult:
        """Migrate single Doppler project to 1Password."""
        self.log(f"\n{'='*60}", force=True)
        self.log(f"Migrating project: {project}", force=True)
        self.log(f"{'='*60}", force=True)

        # Check if item already exists
        if self.onepassword_item_exists(project):
            self.log(f"Skipping '{project}' - already exists in 1Password", force=True)
            return MigrationResult(
                project=project,
                status="skipped",
                key_count=0,
                config_count=0
            )

        # Get all configs for project
        configs = self.get_doppler_configs(project)
        if not configs:
            return MigrationResult(
                project=project,
                status="failed",
                key_count=0,
                config_count=0,
                error="No configs found"
            )

        # Extract secrets from each config
        all_secrets = []
        for config in configs:
            secrets = self.extract_doppler_secrets(project, config)
            if secrets:
                all_secrets.append(secrets)

        if not all_secrets:
            return MigrationResult(
                project=project,
                status="failed",
                key_count=0,
                config_count=len(configs),
                error="No secrets extracted"
            )

        # Merge secrets if multiple configs
        if len(all_secrets) > 1:
            merged_secrets = self.merge_configs_secrets(all_secrets)
        else:
            merged_secrets = all_secrets[0]

        if not merged_secrets:
            return MigrationResult(
                project=project,
                status="failed",
                key_count=0,
                config_count=len(configs),
                error="No secrets after merge"
            )

        # Create 1Password item
        success = self.create_onepassword_item(project, merged_secrets)
        if not success:
            return MigrationResult(
                project=project,
                status="failed",
                key_count=len(merged_secrets),
                config_count=len(configs),
                error="Failed to create 1Password item"
            )

        # Validate creation
        valid, missing = self.validate_onepassword_item(project, set(merged_secrets.keys()))
        if not valid and not self.dry_run:
            return MigrationResult(
                project=project,
                status="success",  # Still mark as success but with warning
                key_count=len(merged_secrets),
                config_count=len(configs),
                error=f"Validation warning: {len(missing)} keys missing"
            )

        self.log(f"✓ Successfully migrated '{project}'", force=True)
        return MigrationResult(
            project=project,
            status="success",
            key_count=len(merged_secrets),
            config_count=len(configs)
        )

    def migrate_all_projects(self, specific_project: Optional[str] = None) -> List[MigrationResult]:
        """Migrate all Doppler projects or a specific project."""
        if specific_project:
            projects = [specific_project]
            self.log(f"Migrating single project: {specific_project}", force=True)
        else:
            projects = self.get_doppler_projects()
            if not projects:
                self.log("No Doppler projects found", force=True)
                return []

            self.log(f"\nFound {len(projects)} Doppler projects to process", force=True)
            if self.dry_run:
                self.log("DRY RUN MODE - No changes will be made\n", force=True)

        results = []
        for i, project in enumerate(projects, 1):
            self.log(f"\nProgress: {i}/{len(projects)}", force=True)
            result = self.migrate_project(project)
            results.append(result)

        return results

    def print_summary(self, results: List[MigrationResult]):
        """Print migration summary."""
        total = len(results)
        success_count = sum(1 for r in results if r.status == "success")
        skipped_count = sum(1 for r in results if r.status == "skipped")
        failed_count = sum(1 for r in results if r.status == "failed")

        print("\n" + "="*60)
        print("Migration Summary")
        print("="*60)
        print(f"Total Projects: {total}")
        print(f"Migrated: {success_count}")
        print(f"Skipped (existing): {skipped_count}")
        print(f"Failed: {failed_count}")
        print("\nDetails:")
        print("-"*60)

        # Group by status
        for status in ["success", "skipped", "failed"]:
            status_results = [r for r in results if r.status == status]
            if status_results:
                print(f"\n{status.upper()}:")
                for result in status_results:
                    symbol = "✓" if status == "success" else "⊘" if status == "skipped" else "✗"
                    details = f"{result.config_count} config{'s' if result.config_count != 1 else ''}, {result.key_count} keys"
                    error_msg = f" - {result.error}" if result.error else ""
                    print(f"  {symbol} {result.project} ({details}){error_msg}")

        print("\n" + "="*60)

        if self.dry_run:
            print("\nDRY RUN COMPLETE - No changes were made")
            print("Run without --dry-run to perform actual migration")
        else:
            print("\nMIGRATION COMPLETE")
            if success_count > 0:
                print(f"\nSuccessfully migrated {success_count} project{'s' if success_count != 1 else ''} to 1Password")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate secrets from Doppler to 1Password",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run                 Preview migration for all projects
  %(prog)s                           Migrate all projects
  %(prog)s --project arrs            Migrate specific project
  %(prog)s --project arrs --dry-run  Preview specific project migration
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without creating items"
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Migrate specific project only"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    args = parser.parse_args()

    # Create migrator
    migrator = SecretMigrator(dry_run=args.dry_run, verbose=args.verbose)

    # Run migration
    results = migrator.migrate_all_projects(specific_project=args.project)

    # Print summary
    if results:
        migrator.print_summary(results)
    else:
        print("No projects to migrate")
        sys.exit(1)

    # Exit with error if any failures
    failed = any(r.status == "failed" for r in results)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
