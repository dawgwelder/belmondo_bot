#!/bin/bash

# 🚨 EMERGENCY ROLLBACK SCRIPT
# Created: 2025-08-27 07:10:18
# Purpose: Quick restoration of belmondo_bot to pre-async state

set -e  # Exit on any error

echo "🚨 EMERGENCY ROLLBACK - Restoring belmondo_bot to pre-async state..."

# Get current directory
CURRENT_DIR=$(pwd)
PROJECT_NAME="belmondo_bot"

# Check if we're in the right directory
if [[ ! -f "main.py" || ! -f "const.py" ]]; then
    echo "❌ Error: Not in belmondo_bot directory!"
    echo "Please run this script from the project root directory."
    exit 1
fi

echo "📍 Current directory: $CURRENT_DIR"

# Method 1: Git rollback (preferred)
echo "🔄 Attempting git rollback..."
if git branch | grep -q "backup/pre-async-optimization"; then
    echo "✅ Found backup branch, rolling back..."
    git stash push -m "Emergency stash before rollback $(date)"
    git checkout backup/pre-async-optimization
    git checkout -b "hotfix/emergency-rollback-$(date +%Y%m%d_%H%M%S)"
    echo "✅ Git rollback complete!"
    echo "📋 You are now on branch: $(git branch --show-current)"
    exit 0
else
    echo "⚠️  Backup branch not found, trying file system restore..."
fi

# Method 2: File system restore
BACKUP_DIR="../belmondo_bot_backup_20250827_071018"
if [[ -d "$BACKUP_DIR" ]]; then
    echo "✅ Found file system backup, restoring..."
    
    # Create a temporary backup of current state
    TEMP_BACKUP="../belmondo_bot_temp_$(date +%Y%m%d_%H%M%S)"
    echo "📦 Creating temporary backup at: $TEMP_BACKUP"
    cp -r . "$TEMP_BACKUP"
    
    # Remove current files (except .git to preserve history)
    echo "🗑️  Removing current files..."
    find . -maxdepth 1 ! -name "." ! -name ".git" ! -name "rollback.sh" -exec rm -rf {} +
    
    # Restore from backup
    echo "📁 Restoring from backup..."
    cp -r "$BACKUP_DIR"/* .
    
    echo "✅ File system restore complete!"
    echo "📦 Temporary backup created at: $TEMP_BACKUP"
    echo "🗑️  You can delete it with: rm -rf $TEMP_BACKUP"
    exit 0
else
    echo "❌ No backup found at: $BACKUP_DIR"
fi

echo "❌ ROLLBACK FAILED!"
echo "Manual recovery required:"
echo "1. Check for other backup directories: ls -la ../belmondo_bot_backup_*"
echo "2. Check git branches: git branch -a"
echo "3. Contact system administrator if needed"
exit 1