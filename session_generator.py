#!/usr/bin/env python3
"""
Generate StringSession for Telethon.
Run this locally to generate your session string.
"""

from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio

async def main():
    """Generate and print StringSession."""
    api_id = int(input("Enter API ID: "))
    api_hash = input("Enter API Hash: ")
    
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()
        
        print("\n" + "="*50)
        print("YOUR STRING SESSION:")
        print("="*50)
        print(session_string)
        print("="*50)
        
        print("\nCopy this string and set it as STRING_SESSION environment variable")
        
if __name__ == "__main__":
    asyncio.run(main())
