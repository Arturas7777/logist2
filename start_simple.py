#!/usr/bin/env python
"""
Simple script to start Django development server
"""
import os
import sys
import subprocess

if __name__ == "__main__":
    # Set Django settings module
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
    
    print("=" * 50)
    print("🚀 Starting Django Development Server")
    print("=" * 50)
    print()
    
    try:
        # Run Django development server
        subprocess.run([
            sys.executable, 
            'manage.py', 
            'runserver', 
            '0.0.0.0:8000'
        ], check=True)
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)





