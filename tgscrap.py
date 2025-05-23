# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.messages import GetHistoryRequest
import json
from datetime import datetime
import pandas as pd
import time
import asyncio
import logging
import os
import shutil
from pathlib import Path
import re
from urllib.parse import urlparse
import argparse

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('telegram_scraper.log', encoding='utf-8'), # Keep log file name for now
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Disable annoying logs from telethon
logging.getLogger('telethon').setLevel(logging.ERROR)
logging.getLogger('telethon.network').setLevel(logging.ERROR)

CONFIG_FILE = "config.json"

# Global config variables to be populated
API_ID = None
API_HASH = None
PHONE = None
DEFAULT_GROUP_ID = None # Default if not provided by config or arg
DEFAULT_TOPIC_ID = None # Default if not provided by config or arg

def load_configuration():
    """Loads configuration from config.json and then environment variables."""
    global API_ID, API_HASH, PHONE, DEFAULT_GROUP_ID, DEFAULT_TOPIC_ID

    config = {}
    # 1. Try to load from config.json
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            logger.info(f"Successfully loaded configuration from {CONFIG_FILE}")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {CONFIG_FILE}: {e}. Will check environment variables.")
            print(f"⚠️ Error reading {CONFIG_FILE}: {e}. Check its format. Trying environment variables...")
        except Exception as e:
            logger.error(f"Error loading {CONFIG_FILE}: {e}. Will check environment variables.")
            print(f"⚠️ Error loading {CONFIG_FILE}: {e}. Trying environment variables...")

    # Helper to get value: config file -> env var -> default (or None)
    def get_config_value(key_json, key_env, is_int=False, default_val=None):
        val = config.get(key_json) # From JSON file
        if val is None:
            val = os.environ.get(key_env) # From environment variable
        
        if val is None:
            return default_val
        
        if is_int:
            try:
                return int(val)
            except ValueError:
                logger.error(f"Error: Configuration value for {key_json}/{key_env} must be an integer. Got '{val}'.")
                print(f"❌ Error: Configuration value {key_json}/{key_env} must be an integer. Got '{val}'.")
                exit(1)
        return val

    # Load essential credentials
    API_ID = get_config_value("telegram_api_id", "TELEGRAM_API_ID", is_int=True)
    API_HASH = get_config_value("telegram_api_hash", "TELEGRAM_API_HASH")
    PHONE = get_config_value("telegram_phone", "TELEGRAM_PHONE")

    # Check if essential credentials are set
    if not API_ID or not API_HASH or not PHONE:
        error_msg = "Critical error: Telegram API_ID, API_HASH, or PHONE is not set."
        logger.critical(error_msg)
        print(f"❌ {error_msg}")
        print(f"Please set them in '{CONFIG_FILE}' or as environment variables (TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE).")
        print(f"A template '{CONFIG_FILE}.example' should exist in the project directory.")
        exit(1)

    # Load optional defaults
    DEFAULT_GROUP_ID = get_config_value("telegram_default_group_id", "TELEGRAM_DEFAULT_GROUP_ID", is_int=True, default_val=-1000000000000) # Your previous hardcoded default
    DEFAULT_TOPIC_ID = get_config_value("telegram_default_topic_id", "TELEGRAM_DEFAULT_TOPIC_ID", is_int=True) # No hardcoded default for topic_id, can be None

    logger.info("Configuration loaded.")

# Script Configuration
BATCH_SIZE = 100
DOWNLOAD_MEDIA = True
CHUNK_SIZE = 1048576  # 1MB
MAX_RETRIES = 3
MAX_MEDIA_SIZE = 100 * 1024 * 1024 # 100MB

client = None # Initialize globally, will be set after config load and arg parsing

# User cache
user_cache = {}

async def get_users_info(current_client, user_ids):
    # Filter only users not in cache
    new_user_ids = [uid for uid in user_ids if uid not in user_cache and uid is not None] # Ensure uid is not None
    
    cached_users = {uid: user_cache[uid] for uid in user_ids if uid in user_cache}
    
    if not new_user_ids:
        # logger.debug("All requested users are already in cache or None.")
        return {uid: user_cache.get(uid, {'username': None, 'first_name': 'Unknown/Deleted', 'last_name': None}) for uid in user_ids}
    
    users_info_from_api = {}
    try:
        if len(new_user_ids) > 10:
            print(f"👥 Fetching info for {len(new_user_ids)} new users...")
        # logger.debug(f"Fetching information for {len(new_user_ids)} new users: {new_user_ids}")
        
        for user_id in new_user_ids:
            if user_id is None: # Should be filtered by now, but as a safeguard
                users_info_from_api[user_id] = {'username': None, 'first_name': 'N/A', 'last_name': None}
                continue
            try:
                user = await current_client.get_entity(user_id)
                user_data = {
                    'username': user.username if hasattr(user, 'username') else None,
                    'first_name': user.first_name if hasattr(user, 'first_name') else None,
                    'last_name': user.last_name if hasattr(user, 'last_name') else None
                }
                user_cache[user_id] = user_data
                users_info_from_api[user_id] = user_data
            except FloodWaitError as e:
                logger.warning(f"API flood limit reached. Waiting {e.seconds} seconds.")
                print(f"⏳ API flood limit reached. Waiting {e.seconds} seconds...")
                await asyncio.sleep(e.seconds)
                # Retry fetching this user_id after waiting
                # This is a simplified retry; a more robust solution might re-add to a queue
                try:
                    user = await current_client.get_entity(user_id)
                    user_data = {
                        'username': user.username if hasattr(user, 'username') else None,
                        'first_name': user.first_name if hasattr(user, 'first_name') else None,
                        'last_name': user.last_name if hasattr(user, 'last_name') else None
                    }
                    user_cache[user_id] = user_data
                    users_info_from_api[user_id] = user_data
                except Exception as ex_retry:
                    logger.error(f"Error fetching user info for {user_id} after FloodWait: {str(ex_retry)}")
                    user_data = {'username': None, 'first_name': 'ErrorFetching', 'last_name': None}
                    user_cache[user_id] = user_data # Cache error state
                    users_info_from_api[user_id] = user_data
            except TypeError as e:
                if "NoneType" in str(e): # Handle cases like "Cannot cast NoneType to any kind of Peer"
                    logger.warning(f"Could not fetch info for user_id {user_id} (likely deleted or system message): {str(e)}")
                    user_data = {'username': None, 'first_name': 'Unknown/Deleted', 'last_name': None}
                else:
                    logger.error(f"TypeError fetching user info for {user_id}: {str(e)}")
                    user_data = {'username': None, 'first_name': 'ErrorFetching', 'last_name': None}
                user_cache[user_id] = user_data # Cache error state
                users_info_from_api[user_id] = user_data
            except Exception as e:
                logger.error(f"Error fetching user info for {user_id}: {str(e)}")
                # Provide a default structure for users that cause errors
                user_data = {'username': None, 'first_name': 'ErrorFetching', 'last_name': None}
                user_cache[user_id] = user_data # Cache error state
                users_info_from_api[user_id] = user_data
            
        # logger.debug(f"Stored {len(users_info_from_api)} new users in cache.")
        # Combine cached users with newly fetched ones
        final_user_info = cached_users
        final_user_info.update(users_info_from_api)
        # Ensure all original user_ids have an entry, even if it's a default for None
        for uid in user_ids:
            if uid not in final_user_info:
                 final_user_info[uid] = {'username': None, 'first_name': 'Unknown/Deleted', 'last_name': None}
        return final_user_info
        
    except FloodWaitError as e: # Outer FloodWait, e.g. when calling get_entity too rapidly in a loop
        logger.warning(f"API flood limit reached during user info batch. Waiting {e.seconds} seconds.")
        print(f"⏳ API flood limit reached. Waiting {e.seconds} seconds...")
        await asyncio.sleep(e.seconds)
        return await get_users_info(current_client, user_ids) # Retry the whole batch
    except Exception as e:
        logger.error(f"General error fetching user information: {str(e)}")
        # Return default for all requested user_ids in case of a general failure
        return {uid: {'username': None, 'first_name': 'ErrorFetching', 'last_name': None} for uid in user_ids}


async def download_media_file(current_client, msg, media_path_base):
    if not msg.media:
        return None
    
    try:
        file_size = 0
        if hasattr(msg.media, 'document') and hasattr(msg.media.document, 'size'):
            file_size = msg.media.document.size
            if file_size > MAX_MEDIA_SIZE:
                logger.debug(f"Skipping large file ({file_size/1024/1024:.1f}MB), message ID: {msg.id}")
                return f"skipped_large_file_({file_size/1024/1024:.1f}MB)"

        file_extension = 'unknown'
        original_filename_attr = None

        if hasattr(msg.media, 'photo'):
            file_extension = 'jpg'
        elif hasattr(msg.media, 'document'):
            mime_type = msg.media.document.mime_type if hasattr(msg.media.document, 'mime_type') else ''
            
            # Try to get original filename for extension
            if hasattr(msg.media.document, 'attributes'):
                for attr in msg.media.document.attributes:
                    if hasattr(attr, 'file_name'):
                        original_filename_attr = attr.file_name
                        if '.' in original_filename_attr:
                            file_extension = original_filename_attr.split('.')[-1].lower()
                        break # Break from the loop once filename attribute is found
            
            if file_extension == 'unknown' and mime_type: # Fallback to mime_type if filename didn't provide ext
                if 'image/jpeg' in mime_type: file_extension = 'jpg'
                elif 'image/png' in mime_type: file_extension = 'png'
                elif 'image/gif' in mime_type: file_extension = 'gif'
                elif 'image/webp' in mime_type: file_extension = 'webp'
                elif 'video/mp4' in mime_type: file_extension = 'mp4'
                elif 'video/webm' in mime_type: file_extension = 'webm'
                elif 'audio/mpeg' in mime_type: file_extension = 'mp3' # Covers mp3
                elif 'audio/ogg' in mime_type: file_extension = 'ogg'
                elif 'application/pdf' in mime_type: file_extension = 'pdf'
                elif 'application/zip' in mime_type: file_extension = 'zip'
                else: # Generic extension from mime type
                    part = mime_type.split('/')[-1]
                    part = part.split('+')[0] # e.g. svg+xml -> svg
                    if len(part) < 10: file_extension = part # Avoid overly long extensions

            # Sanitize dangerous extensions potentially derived from filename
            if file_extension in ['exe', 'bat', 'cmd', 'msi', 'dll', 'sys', 'sh', 'js']:
                file_extension = f"unsafe_{file_extension}"

        # Generate a unique filename
        timestamp_part = int(time.time()*1000) # Milliseconds for more uniqueness
        unique_filename_base = f"{msg.id}_{timestamp_part}"
        
        # Try to use original filename if available and safe
        final_filename_to_save = f"{unique_filename_base}.{file_extension}"
        if original_filename_attr:
            # Sanitize original_filename_attr for safe use in filesystem
            safe_original_filename = re.sub(r'[^\w\-\._ ]', '_', original_filename_attr)
            # Prepend unique part to avoid collisions but keep original name for readability
            final_filename_to_save = f"{unique_filename_base}_{safe_original_filename}"
            # Ensure it still has an extension if the original was complex
            if '.' not in final_filename_to_save.split('_')[-1]: # Check last part after unique_id
                 final_filename_to_save = f"{final_filename_to_save}.{file_extension}"


        filepath = os.path.join(media_path_base, final_filename_to_save)
        
        if os.path.exists(filepath):
            logger.debug(f"File already exists: {final_filename_to_save}")
            return final_filename_to_save
            
        await current_client.download_media(msg.media, filepath)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # logger.debug(f"Downloaded: {final_filename_to_save}")
            return final_filename_to_save
        elif os.path.exists(filepath) and file_size == 0 and os.path.getsize(filepath) == 0: # Case of 0-byte file
             # logger.debug(f"Downloaded 0-byte file: {final_filename_to_save}")
             return final_filename_to_save
        else:
            logger.warning(f"Failed to download or file is empty: {final_filename_to_save} (Message ID: {msg.id})")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e_rem:
                    logger.error(f"Could not remove failed download {filepath}: {e_rem}")
            return None
            
    except Exception as e:
        logger.error(f"Error downloading media for message {msg.id}: {str(e)}")
        return None

async def run_scraper(target_group_id, target_topic_id=None):
    global client # Use the globally initialized client
    print("🚀 Telegram Scraper")
    print("=" * 50)
    
    print("📡 Connecting to Telegram...")
    if not client: # Should have been initialized in __main__
        logger.error("Telegram client not initialized!")
        return
        
    await client.connect()

    if not await client.is_user_authorized():
        print("🔐 Authorization required")
        await client.send_code_request(PHONE)
        code = input('📱 Enter SMS code: ')
        try:
            await client.sign_in(PHONE, code)
            print("✅ Successfully logged in")
        except SessionPasswordNeededError:
            password = input('🔑 Enter 2FA password: ')
            await client.sign_in(password=password)
            print("✅ Successfully logged in with 2FA")

    print(f"📋 Fetching group/channel info for ID: {target_group_id}...")
    try:
        entity = await client.get_entity(target_group_id)
    except ValueError as e:
        logger.error(f"Could not find the group/channel with ID {target_group_id}. Make sure the ID is correct and your account has access. Error: {e}")
        print(f"❌ Error: Could not find group/channel with ID {target_group_id}. Please check the ID and your access rights.")
        return
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching entity for group ID {target_group_id}: {e}")
        print(f"❌ Error: An unexpected error occurred for group ID {target_group_id}.")
        return


    if target_topic_id:
        print(f"💬 Starting download for Topic ID {target_topic_id} in Group ID {target_group_id}")
        output_base_dir = Path("output") / f"group_{target_group_id}" / f"topic_{target_topic_id}"
    else:
        print(f"💬 Starting download for entire Group/Channel ID {target_group_id}")
        output_base_dir = Path("output") / f"group_{target_group_id}" / "complete_archive"
    
    media_dir = output_base_dir / "media"
    os.makedirs(media_dir, exist_ok=True)
    print(f"📁 Output directory: {output_base_dir}")

    total_messages = 0
    start_time = time.time()
    all_user_ids = set()
    
    json_path = output_base_dir / 'archive.json'
    excel_path = output_base_dir / 'archive.xlsx'
    
    # Check if archive.json exists and load existing message IDs to avoid reprocessing
    existing_message_ids = set()
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as jf:
                # Attempt to load as a list of objects
                content = jf.read()
                if content.strip(): # Check if file is not empty
                    # Handle cases: might be a list or list-like structure, or one object per line (not our case)
                    # For a list of JSON objects:
                    if content.strip().startswith('[') and content.strip().endswith(']'):
                        try:
                            data = json.loads(content)
                            for item in data:
                                if isinstance(item, dict) and 'id' in item:
                                    existing_message_ids.add(item['id'])
                            print(f"ℹ️ Found {len(existing_message_ids)} existing messages in {json_path}. Will skip them.")
                        except json.JSONDecodeError as e:
                            logger.warning(f"Could not parse existing JSON file {json_path} to resume: {e}. Starting fresh for this file.")
                            existing_message_ids.clear() # Start fresh if parsing fails
                    else: # If not a valid JSON array structure, assume problematic and start fresh for safety
                        logger.warning(f"Existing JSON file {json_path} is not a valid JSON array. Starting fresh for this file.")
                        existing_message_ids.clear()
        except Exception as e:
            logger.error(f"Error reading existing archive {json_path}: {e}. Starting fresh.")
            existing_message_ids.clear()


    # We will append to JSON file in batches.
    # For Excel, it's more complex to append, so we'll collect all new data and write at the end or overwrite.
    # For simplicity, this version will collect all NEW messages for Excel and write them.
    # If resuming, it means the Excel file might be an older full version or partial.
    # A robust resume for Excel would require reading it, merging, and rewriting, which is more complex.
    # Current approach: JSON is appended (if valid), Excel is overwritten with all messages (old+new) or just new if starting fresh.
    # To make it simpler: we'll write JSON incrementally (new messages). Excel will be written once at the end with ALL messages (including previously existing if any).

    all_messages_for_session = [] # Store all messages (old+new) for final Excel write & JSON reconstruction
    if json_path.exists() and existing_message_ids: # Load existing messages if we are resuming
        try:
            with open(json_path, 'r', encoding='utf-8') as jf:
                content = jf.read()
                if content.strip().startswith('[') and content.strip().endswith(']'):
                    all_messages_for_session = json.loads(content)
        except Exception as e:
            logger.warning(f"Could not reload messages from existing {json_path} for merging: {e}")
            all_messages_for_session = [] # Start with empty if reload fails


    # Open JSON in append mode if exists and seems valid, otherwise write mode.
    # This is tricky with the `[` and `]` array structure.
    # A better approach for incremental JSON is one JSON object per line (JSONL).
    # For now, we'll collect in memory and write at the end.
    
    newly_processed_messages_data = []
    processed_in_batch_for_excel = 0

    print("⏳ Processing messages...")
    messages_processed_this_run = 0
    
    # Determine reply_to based on whether target_topic_id is provided
    reply_to_id = target_topic_id if target_topic_id else None

    async for msg in client.iter_messages(entity, limit=None, reply_to=reply_to_id): # limit=None to get all
        if msg.id in existing_message_ids:
            # logger.debug(f"Skipping already processed message ID: {msg.id}")
            continue # Skip this message

        try:
            messages_processed_this_run +=1
            if messages_processed_this_run % 50 == 0 and messages_processed_this_run > 0:
                elapsed_time_batch = time.time() - start_time
                rate_batch = messages_processed_this_run / elapsed_time_batch if elapsed_time_batch > 0 else 0
                print(f"📊 Processed: {messages_processed_this_run} new messages this run | Rate: {rate_batch:.1f} msg/s")
            
            # logger.debug(f"Processing message ID: {msg.id}")
            if msg.sender_id:
                all_user_ids.add(msg.sender_id)
                
            # Fetch user info (can be batched later for optimization if this becomes a bottleneck)
            # For now, fetch one by one, relying on user_cache
            user_info_map = await get_users_info(client, [msg.sender_id] if msg.sender_id else [])
            user_data = user_info_map.get(msg.sender_id, {'username': None, 'first_name': 'N/A', 'last_name': None})
                
            media_filename = None
            if DOWNLOAD_MEDIA and msg.media:
                for attempt in range(MAX_RETRIES):
                    media_filename = await download_media_file(client, msg, media_dir)
                    if media_filename: # Includes "skipped_large_file" string
                        if "skipped_large_file" in media_filename:
                            # logger.info(f"Media skipped for msg {msg.id}: {media_filename}")
                            pass # Already logged in download_media_file
                        break # Break from retry loop if media processed (downloaded, skipped, or failed definitively by download_media_file)
                    # logger.debug(f"Retrying media download for msg {msg.id}, attempt {attempt+1}")
                    await asyncio.sleep(1) # Wait before retrying
            
            message_data = {
                'id': msg.id,
                'date': msg.date.isoformat() if msg.date else None, # Store in ISO format
                'sender_id': msg.sender_id,
                'sender_username': user_data.get('username'),
                'sender_first_name': user_data.get('first_name'),
                'sender_last_name': user_data.get('last_name'),
                'text': msg.text.replace('\\n', ' ') if msg.text else '', # Normalize newlines
                'has_media': bool(msg.media),
                'media_filename': media_filename, # Can be None, actual filename, or "skipped..."
                'has_links': 'http' in msg.text if msg.text else False, # Basic link detection
                'reply_to_message_id': msg.reply_to_msg_id if msg.reply_to and msg.reply_to.reply_to_msg_id else None
            }
            
            newly_processed_messages_data.append(message_data)
            total_messages += 1 # Counts all messages (new and potentially existing if we were to merge fully)
                                # This 'total_messages' might be confusing if we are just appending.
                                # Let's rename it to 'newly_fetched_messages_count'
            
            # Batch saving for Excel (still collects all new messages in memory)
            # if len(newly_processed_messages_data) % BATCH_SIZE == 0:
            #     # This was for incremental Excel write, which is complex.
            #     # We will write Excel once at the end.
            #     pass
                
        except Exception as e:
            logger.error(f"Error processing message ID {msg.id}: {str(e)}")
            continue
    
    # Combine newly processed messages with existing ones for the final list
    final_message_list = []
    existing_ids_for_update = {m['id'] for m in all_messages_for_session}
    
    for m_exist in all_messages_for_session:
        final_message_list.append(m_exist)
        
    for m_new in newly_processed_messages_data:
        if m_new['id'] not in existing_ids_for_update: # Add if truly new
            final_message_list.append(m_new)
            
    # Sort all messages by ID (or date) before writing
    final_message_list.sort(key=lambda x: x['id'])


    # Write/overwrite the JSON file with all (old + new) messages
    try:
        with open(json_path, 'w', encoding='utf-8') as json_file_final:
            json.dump(final_message_list, json_file_final, ensure_ascii=False, indent=2)
        print(f"💾 JSON archive saved: {json_path} ({len(final_message_list)} total messages)")
    except Exception as e:
        logger.error(f"Error writing final JSON to {json_path}: {e}")

    # Write/overwrite the Excel file
    if final_message_list:
        try:
            df = pd.DataFrame(final_message_list)
            # Reorder columns for better readability in Excel
            excel_columns = ['id', 'date', 'sender_id', 'sender_username', 'sender_first_name', 'sender_last_name', 
                             'text', 'has_media', 'media_filename', 'has_links', 'reply_to_message_id']
            # Filter df columns to only those that exist in the DataFrame to prevent KeyErrors
            df_excel = df[[col for col in excel_columns if col in df.columns]]
            
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer_final:
                 df_excel.to_excel(writer_final, index=False, sheet_name='Messages')
            print(f"📊 Excel archive saved: {excel_path} ({len(final_message_list)} total messages)")
            del df # Free memory
            del df_excel
        except Exception as e:
            logger.error(f"Error writing Excel file to {excel_path}: {e}")
    else:
        print("ℹ️ No messages to save to Excel.")

    
    elapsed_time = time.time() - start_time
    
    print("\n" + "=" * 50)
    print("🎉 SCRAPING COMPLETED SUCCESSFULLY!")
    print("=" * 50)
    print(f"📊 Statistics for this run:")
    print(f"   • New messages fetched: {len(newly_processed_messages_data)}")
    print(f"   • Total messages in archive: {len(final_message_list)}")
    print(f"   • Execution time: {elapsed_time:.1f}s")
    if elapsed_time > 0 and len(newly_processed_messages_data) > 0:
        print(f"   • Average rate (new messages): {len(newly_processed_messages_data)/elapsed_time:.1f} messages/s")
    print(f"\n📁 Output files in: {output_base_dir}")
    print(f"   • 📄 JSON: {json_path.name}")
    print(f"   • 📊 Excel: {excel_path.name}")
    if DOWNLOAD_MEDIA:
        try:
            media_count = len([f for f in os.listdir(media_dir) if os.path.isfile(os.path.join(media_dir, f))])
            print(f"   • 🖼️  Media: {media_count} files in {media_dir.name}/ directory")
        except FileNotFoundError:
            print(f"   • 🖼️  Media: media directory not found (expected at {media_dir})")
            
    print(f"\n📝 Detailed logs: telegram_scraper.log")
    print("=" * 50)

if __name__ == '__main__':
    # Call load_configuration() at the very beginning of the main execution block
    load_configuration()

    parser = argparse.ArgumentParser(description="Telegram Scraper for channels/groups and topics.")
    # Arguments should now use the loaded DEFAULT_GROUP_ID and DEFAULT_TOPIC_ID if applicable
    # Or, if an argument is provided, it should override the default from config.
    parser.add_argument("--group_id", type=int, required=False, 
                        help=f"Target Group/Channel ID. Overrides config/env. (Default from config: {DEFAULT_GROUP_ID})")
    parser.add_argument("--topic_id", type=int, required=False, 
                        help=f"Specific Topic ID. Overrides config/env. (Default from config: {DEFAULT_TOPIC_ID})")
    
    args = parser.parse_args()

    # Determine the final group_id and topic_id to use
    # Priority: command-line arg > config/env default
    target_group_id = args.group_id if args.group_id is not None else DEFAULT_GROUP_ID
    target_topic_id = args.topic_id if args.topic_id is not None else DEFAULT_TOPIC_ID

    if target_group_id is None: # Should only happen if not set in config, env, or as arg, and no hardcoded default
        print("❌ Error: Group ID is not specified. Provide --group_id, or set in config.json or TELEGRAM_DEFAULT_GROUP_ID env var.")
        exit(1)

    # Initialize Telegram client (needs to be done after API_ID and API_HASH are loaded)
    client = TelegramClient('session_telegram', API_ID, API_HASH)

    try:
        with client:
            client.loop.run_until_complete(run_scraper(target_group_id, target_topic_id))
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
        print(f"❌ A critical error occurred: {e}")
