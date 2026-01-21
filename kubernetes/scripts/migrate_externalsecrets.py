#!/usr/bin/env python3
"""
ExternalSecrets Migration Script: Doppler → 1Password

Migrates Kubernetes ExternalSecret manifests from Doppler to 1Password.
Handles multiple patterns including standard apps, arrs multi-app, CNPG data arrays, etc.

Usage:
    ./migrate_externalsecrets.py --dry-run                    # Preview all changes
    ./migrate_externalsecrets.py --apps searxng,palmr        # Migrate specific apps
    ./migrate_externalsecrets.py --pattern standard          # Migrate all standard apps
    ./migrate_externalsecrets.py --diff-only > diffs.txt     # Generate diff report
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# Initialize YAML parser with formatting preservation
yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False
yaml.width = 4096


class ExternalSecretMigrator:
    """Migrates ExternalSecret manifests from Doppler to 1Password."""

    # Special cases that need custom handling
    ARRS_APPS = ['autobrr', 'bazarr', 'prowlarr', 'radarr', 'readarr', 'sonarr']
    DATA_ARRAY_APPS = ['cloudnative-pg']
    TEMPLATEFROM_APPS = ['kometa']
    MULTI_RESOURCE_APPS = ['cookjam']

    # Already migrated apps (skip these)
    MIGRATED_APPS = ['karakeep', 'qui', 'onepassword']

    def __init__(self, repo_root: Path, dry_run: bool = False, verbose: bool = False):
        self.repo_root = repo_root
        self.apps_dir = repo_root / "kubernetes" / "apps"
        self.dry_run = dry_run
        self.verbose = verbose
        self.stats = {
            'processed': 0,
            'skipped': 0,
            'errors': 0,
            'modified': 0
        }

    def log(self, msg: str, level: str = "INFO"):
        """Print log message."""
        if level == "DEBUG" and not self.verbose:
            return
        print(f"[{level}] {msg}", file=sys.stderr)

    def find_externalsecret_files(self) -> List[Path]:
        """Find all externalsecret.yaml files in the apps directory."""
        files = list(self.apps_dir.glob("**/externalsecret.yaml"))
        self.log(f"Found {len(files)} ExternalSecret files")
        return files

    def is_doppler_externalsecret(self, docs: List[Dict]) -> bool:
        """Check if YAML contains Doppler-based ExternalSecret."""
        for doc in docs:
            if not doc:
                continue
            if doc.get('kind') == 'ClusterSecretStore':
                provider = doc.get('spec', {}).get('provider', {})
                if 'doppler' in provider:
                    return True
            if doc.get('kind') == 'ExternalSecret':
                # Check if it references doppler (might not have ClusterSecretStore in same file)
                store_ref = doc.get('spec', {}).get('secretStoreRef', {})
                if store_ref.get('name') not in ['onepassword', 'doppler']:
                    # Likely a custom store name that's doppler-based
                    return True
                if 'find' in str(doc.get('spec', {}).get('dataFrom', [])):
                    return True
        return False

    def load_yaml_file(self, file_path: Path) -> Tuple[List[Dict], str]:
        """Load YAML file and return documents + raw content."""
        with open(file_path, 'r') as f:
            content = f.read()

        docs = list(yaml.load_all(content))
        return docs, content

    def save_yaml_file(self, file_path: Path, docs: List[Dict]):
        """Save YAML documents to file."""
        if self.dry_run:
            self.log(f"DRY-RUN: Would write {file_path}", "DEBUG")
            return

        # Create backup
        backup_path = file_path.with_suffix('.yaml.bak')
        with open(file_path, 'r') as f:
            with open(backup_path, 'w') as bf:
                bf.write(f.read())

        # Write new content
        with open(file_path, 'w') as f:
            yaml.dump_all(docs, f)

        self.log(f"Wrote {file_path} (backup: {backup_path})")

    def extract_app_name(self, file_path: Path) -> str:
        """Extract app name from file path."""
        # Path format: kubernetes/apps/<namespace>/<app>/app/externalsecret.yaml
        parts = file_path.parts
        if 'apps' in parts:
            apps_idx = parts.index('apps')
            if apps_idx + 2 < len(parts):
                return parts[apps_idx + 2]
        return file_path.parent.parent.name

    def remove_clustersecretstore(self, docs: List[Dict]) -> List[Dict]:
        """Remove ClusterSecretStore resources from document list."""
        filtered = []
        for doc in docs:
            if not doc:
                continue
            if doc.get('kind') != 'ClusterSecretStore':
                filtered.append(doc)
            else:
                self.log(f"Removing ClusterSecretStore: {doc.get('metadata', {}).get('name')}")
        return filtered

    def get_1password_key(self, app_name: str) -> str:
        """Get 1Password item key for an app (handles arrs special case)."""
        if app_name in self.ARRS_APPS:
            return 'arrs'
        return app_name

    def get_rewrite_pattern(self, app_name: str) -> Dict:
        """Get rewrite regexp pattern for an app."""
        # Replace hyphens with underscores for valid template variable names
        app_name_clean = app_name.replace('-', '_')

        if app_name in self.ARRS_APPS:
            # Arrs: filter only app-specific fields (e.g., "sonarr__.*")
            return {
                'regexp': {
                    'source': f"{app_name}__(.*)",
                    'target': f"{app_name_clean}_$1"
                }
            }
        else:
            # Standard: prefix all fields with app name
            return {
                'regexp': {
                    'source': "(.*)",
                    'target': f"{app_name_clean}_$1"
                }
            }

    def convert_template_variable(self, var: str, app_name: str) -> str:
        """
        Convert template variable from Doppler to 1Password format.

        Examples:
            {{ .NEXTAUTH_SECRET }} → {{ .langfuse_nextauth_secret }}
            {{ .SONARR__API_KEY }} → {{ .sonarr_api_key }}
            {{ .LANGFUSE_POSTGRES_USER }} → {{ .langfuse_langfuse_postgres_user }}
        """
        # Extract variable name from template syntax - handle both {{ .VAR }} and plain VAR
        match = re.search(r'\{\{\s*\.(\w+)\s*\}\}', var)
        if not match:
            # Try without template syntax
            if re.match(r'^\w+$', var):
                var_name = var
            else:
                return var
        else:
            var_name = match.group(1)

        # Convert to lowercase
        var_lower = var_name.lower()

        # Replace hyphens with underscores for valid template variable names
        app_name_clean = app_name.replace('-', '_')

        # Handle arrs special case: SONARR__API_KEY → sonarr_api_key
        if app_name in self.ARRS_APPS:
            # Remove double underscore, add app prefix
            var_lower = var_lower.replace('__', '_')
            if not var_lower.startswith(f"{app_name_clean}_"):
                var_lower = f"{app_name_clean}_{var_lower}"
        else:
            # Standard: prefix with app name
            var_lower = f"{app_name_clean}_{var_lower}"

        return f"{{{{ .{var_lower} }}}}"

    def update_template_data(self, template_data: Dict, app_name: str) -> Dict:
        """Recursively update template variables in data dict."""
        if isinstance(template_data, dict):
            result = CommentedMap()
            for key, value in template_data.items():
                if isinstance(value, str):
                    result[key] = self.convert_template_variable(value, app_name)
                else:
                    result[key] = self.update_template_data(value, app_name)

            # Preserve comments if available (works with CommentedMap from ruamel.yaml)
            # We don't need to manually copy comments - ruamel.yaml handles this
            # when we iterate over the original dict

            return result
        elif isinstance(template_data, list):
            return [self.update_template_data(item, app_name) for item in template_data]
        else:
            return template_data

    def migrate_standard_externalsecret(self, es: Dict, app_name: str) -> Dict:
        """Migrate standard ExternalSecret to 1Password format."""
        # Update secretStoreRef
        es['spec']['secretStoreRef'] = CommentedMap({
            'kind': 'ClusterSecretStore',
            'name': 'onepassword'
        })

        # Add refreshInterval
        spec = es['spec']
        if 'refreshInterval' not in spec:
            # Insert at beginning of spec
            new_spec = CommentedMap()
            new_spec['refreshInterval'] = '5m'
            for k, v in spec.items():
                new_spec[k] = v
            es['spec'] = new_spec

        # Update target
        target = es['spec'].get('target', CommentedMap())

        # Add creationPolicy
        if 'creationPolicy' not in target:
            target['creationPolicy'] = 'Owner'

        # Remove engineVersion from template
        if 'template' in target:
            template = target['template']
            if 'engineVersion' in template:
                del template['engineVersion']

            # Update template variables
            if 'data' in template:
                template['data'] = self.update_template_data(template['data'], app_name)

        es['spec']['target'] = target

        # Update dataFrom
        op_key = self.get_1password_key(app_name)
        rewrite_pattern = self.get_rewrite_pattern(app_name)

        es['spec']['dataFrom'] = [
            CommentedMap({
                'extract': CommentedMap({'key': op_key}),
                'rewrite': [rewrite_pattern]
            })
        ]

        return es

    def migrate_cnpg_externalsecret(self, es: Dict, app_name: str) -> Dict:
        """Migrate CNPG ExternalSecret (data array pattern)."""
        # Update secretStoreRef
        es['spec']['secretStoreRef'] = CommentedMap({
            'kind': 'ClusterSecretStore',
            'name': 'onepassword'
        })

        # Add refreshInterval
        spec = es['spec']
        if 'refreshInterval' not in spec:
            new_spec = CommentedMap()
            new_spec['refreshInterval'] = '5m'
            for k, v in spec.items():
                new_spec[k] = v
            es['spec'] = new_spec

        # Update target - remove engineVersion
        target = es['spec'].get('target', CommentedMap())
        if 'template' in target and 'engineVersion' in target['template']:
            del target['template']['engineVersion']

        # Update data array remoteRef format
        if 'data' in es['spec']:
            for item in es['spec']['data']:
                if 'remoteRef' in item and 'key' in item['remoteRef']:
                    old_key = item['remoteRef']['key']
                    # Convert POSTGRES_SUPER_USER → postgres_super_user
                    property_name = old_key.lower()

                    item['remoteRef'] = CommentedMap({
                        'key': app_name,
                        'property': property_name
                    })

        return es

    def migrate_file(self, file_path: Path, app_filter: Optional[List[str]] = None) -> bool:
        """
        Migrate a single ExternalSecret file.

        Returns True if file was modified, False if skipped.
        """
        app_name = self.extract_app_name(file_path)

        # Check if already migrated
        if app_name in self.MIGRATED_APPS:
            self.log(f"Skipping {app_name} (already migrated)")
            self.stats['skipped'] += 1
            return False

        # Check app filter
        if app_filter and app_name not in app_filter:
            self.log(f"Skipping {app_name} (not in filter)", "DEBUG")
            self.stats['skipped'] += 1
            return False

        try:
            self.log(f"Processing {app_name} ({file_path.relative_to(self.repo_root)})")

            # Load file
            docs, original_content = self.load_yaml_file(file_path)

            # Check if Doppler-based
            if not self.is_doppler_externalsecret(docs):
                self.log(f"Skipping {app_name} (not Doppler-based or already 1Password)")
                self.stats['skipped'] += 1
                return False

            # Remove ClusterSecretStore
            docs = self.remove_clustersecretstore(docs)

            # Migrate each ExternalSecret
            for i, doc in enumerate(docs):
                if not doc or doc.get('kind') != 'ExternalSecret':
                    continue

                self.log(f"Migrating ExternalSecret: {doc.get('metadata', {}).get('name')}")

                # Choose migration strategy based on app
                if app_name in self.DATA_ARRAY_APPS:
                    docs[i] = self.migrate_cnpg_externalsecret(doc, app_name)
                else:
                    docs[i] = self.migrate_standard_externalsecret(doc, app_name)

            # Save file
            self.save_yaml_file(file_path, docs)

            self.stats['processed'] += 1
            self.stats['modified'] += 1
            return True

        except Exception as e:
            self.log(f"ERROR processing {app_name}: {e}", "ERROR")
            self.stats['errors'] += 1
            import traceback
            traceback.print_exc()
            return False

    def migrate_all(self, app_filter: Optional[List[str]] = None):
        """Migrate all ExternalSecret files."""
        files = self.find_externalsecret_files()

        for file_path in sorted(files):
            self.migrate_file(file_path, app_filter)

        # Print summary
        print("\n" + "="*60)
        print("Migration Summary")
        print("="*60)
        print(f"Processed:  {self.stats['processed']}")
        print(f"Modified:   {self.stats['modified']}")
        print(f"Skipped:    {self.stats['skipped']}")
        print(f"Errors:     {self.stats['errors']}")
        print("="*60)

        if self.dry_run:
            print("\nDRY RUN - No files were modified")
        else:
            print(f"\nBackup files created with .yaml.bak extension")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate ExternalSecrets from Doppler to 1Password",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview all changes
  ./migrate_externalsecrets.py --dry-run

  # Migrate specific apps
  ./migrate_externalsecrets.py --apps searxng,palmr,radicale

  # Migrate all in verbose mode
  ./migrate_externalsecrets.py --verbose

  # Generate diff report
  ./migrate_externalsecrets.py --dry-run --verbose > migration_report.txt
        """
    )

    parser.add_argument(
        '--apps',
        help='Comma-separated list of app names to migrate',
        type=str
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying files'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )

    parser.add_argument(
        '--repo-root',
        type=Path,
        default=Path(__file__).parent.parent.parent,
        help='Path to repository root (default: auto-detect)'
    )

    args = parser.parse_args()

    # Parse app filter
    app_filter = None
    if args.apps:
        app_filter = [app.strip() for app in args.apps.split(',')]

    # Create migrator
    migrator = ExternalSecretMigrator(
        repo_root=args.repo_root,
        dry_run=args.dry_run,
        verbose=args.verbose
    )

    # Run migration
    migrator.migrate_all(app_filter)


if __name__ == '__main__':
    main()
