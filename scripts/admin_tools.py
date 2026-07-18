#!/usr/bin/env python3
"""Admin tools for SmartRouter - command line utilities for admin management."""

import argparse
import getpass
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from smart_router.auth.superadmin import (
    list_users,
    add_user,
    update_user,
    delete_user,
    save_admin_config,
    load_admin_config,
)


def change_password(username: str, new_password: str = None):
    """Change password for a user."""
    if not new_password:
        # Interactive mode
        new_password = getpass.getpass(f"Enter new password for '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if new_password != confirm:
            print("Error: Passwords do not match")
            return False

    if len(new_password) < 6:
        print("Error: Password must be at least 6 characters")
        return False

    # Update user password
    success = update_user(username, {"password": new_password})
    if success:
        print(f"Password changed successfully for user '{username}'")
        return True
    else:
        print(f"Failed to change password for user '{username}' (user not found?)")
        return False


def transfer_superadmin(new_admin_username: str):
    """Transfer super admin role to another user."""
    users = list_users()

    # Check if target user exists
    target_exists = any(u.get("username") == new_admin_username for u in users)
    if not target_exists:
        print(f"Error: User '{new_admin_username}' not found")
        return False

    # Find current super admin
    current_admin = None
    for u in users:
        if u.get("role") == "admin":
            current_admin = u.get("username")
            break

    # Update roles
    update_user(new_admin_username, {"role": "admin"})
    if current_admin and current_admin != new_admin_username:
        update_user(current_admin, {"role": "admin"})

    print(f"Super admin role transferred from '{current_admin}' to '{new_admin_username}'")
    return True


def list_all_users():
    """List all users."""
    users = list_users()
    if not users:
        print("No users found")
        return

    print("\nUsers:")
    print("-" * 60)
    for u in users:
        username = u.get("username", "unknown")
        role = u.get("role", "user")
        tenant_id = u.get("tenant_id", "")
        print(f"  {username:20s} | Role: {role:10s} | Tenant: {tenant_id}")
    print("-" * 60)


def main():
    parser = argparse.ArgumentParser(description="SmartRouter Admin Tools")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # change-password command
    pwd_parser = subparsers.add_parser("change-password", help="Change user password")
    pwd_parser.add_argument("--username", "-u", help="Username")
    pwd_parser.add_argument("--password", "-p", help="New password (will prompt if not provided)")

    # transfer-superadmin command
    transfer_parser = subparsers.add_parser("transfer-superadmin", help="Transfer super admin role")
    transfer_parser.add_argument("--to", "-t", dest="new_admin", help="New super admin username")

    # list-users command
    subparsers.add_parser("list-users", help="List all users")

    args = parser.parse_args()

    if args.command == "change-password":
        if not args.username:
            args.username = input("Username: ")
        change_password(args.username, args.password)

    elif args.command == "transfer-superadmin":
        if not args.new_admin:
            args.new_admin = input("New super admin username: ")
        transfer_superadmin(args.new_admin)

    elif args.command == "list-users":
        list_all_users()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
