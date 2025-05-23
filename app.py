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

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
import json
import os
import shutil
from datetime import datetime
import pandas as pd
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

app = Flask(__name__)

# Mapping of topic IDs to their names (example, can be customized or loaded dynamically)
# This might need to be adapted if you have many groups or want more dynamic topic naming.
TOPIC_NAMES = {
    '-100GROUPID1': { # Example Group ID
        'DEFAULT_NAME': 'Example Group Archive', # Name for the whole group archive
        '123': 'General Discussion',
        '456': 'Project Updates', 
        '789': 'Resources',
        '101': 'Documentation',
        '102': 'Questions',
        '103': 'Announcements',
        '104': 'Random',
        '105': 'Important'
    }
    # Add other group IDs and their topics here if needed
}

OUTPUT_DIR = "output" # Base directory for archives

def get_archive_display_name(group_id_str, topic_id_str=None):
    """Returns a display name for a group or a specific topic within a group."""
    group_id_str = str(group_id_str)
    group_info = TOPIC_NAMES.get(group_id_str, {})
    
    if topic_id_str:
        topic_id_str = str(topic_id_str)
        return group_info.get(topic_id_str, f"Topic {topic_id_str}")
    else:
        # For the whole group archive
        return group_info.get('DEFAULT_NAME', f"Group {group_id_str} Archive")

def hash_color(text):
    hash_object = hashlib.md5(str(text).encode()) # Ensure text is string
    hash_hex = hash_object.hexdigest()
    color = f"#{hash_hex[:6]}"
    return color

app.jinja_env.filters['hash_color'] = hash_color

def convert_links_to_html(text):
    if not text:
        return ""
    # Markdown links [label](url)
    text = re.sub(r'\[([^\]]+?)\]\((https?://[^\s)]+)\)', 
                  r'<a href="\2" target="_blank" class="chat-link">\1</a>', text)
    # Naked URLs, but not if already in an href attribute
    text = re.sub(r'(?<!href=["a-zA-Z0-9])(https?://(?:[\w\-./?=&%:]|(?:%[\da-fA-F]{2}))+)', 
                  r'<a href="\1" target="_blank" class="chat-link">\1</a>', text)
    return text

def format_telegram_style(text):
    if not text:
        return ""
    text = str(text) # Ensure text is a string
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text) # Bold
    text = re.sub(r'__(.+?)__', r'<em>\1</em>', text) # Italics (double underscore)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<em>\1</em>', text) # Single underscore italics, avoid __
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text) # Strikethrough
    text = re.sub(r'```(?:[a-zA-Z_0-9\-]*\n)?([\s\S]+?)```', r'<pre><code>\1</code></pre>', text, flags=re.MULTILINE) # Code blocks
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text) # Inline code
    # Links are handled by convert_links_to_html, called separately
    text = text.replace('\n', '<br>') # Newlines to <br>
    return text

def load_messages(group_id, topic_id=None):
    group_id_str = str(group_id).replace("group_","")
    
    if topic_id:
        topic_id_str = str(topic_id).replace("topic_","")
        archive_path = Path(OUTPUT_DIR) / f"group_{group_id_str}" / f"topic_{topic_id_str}" / "archive.json"
    else:
        archive_path = Path(OUTPUT_DIR) / f"group_{group_id_str}" / "complete_archive" / "archive.json"
    
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    with open(archive_path, 'r', encoding='utf-8') as f:
        messages = json.load(f)
    
    for msg in messages:
        date_val = msg.get('date')
        if date_val:
            try:
                msg['date_dt'] = datetime.fromisoformat(str(date_val).replace('Z', '+00:00'))
            except (ValueError, TypeError):
                msg['date_dt'] = datetime.now() 
                msg['date_str'] = str(date_val) # Keep original problematic string for display
        else:
            msg['date_dt'] = datetime.now()
            msg['date_str'] = "No Date Available"

        msg_text = msg.get('text', '')
        # IMPORTANT: Apply link conversion BEFORE other formatting to avoid breaking link structure
        html_text = convert_links_to_html(msg_text) 
        msg['text_html'] = format_telegram_style(html_text) 

    messages.sort(key=lambda x: x['date_dt'])
    return messages

@app.route('/')
def index():
    # Scan the OUTPUT_DIR for available group archives and their topics
    archived_items = []
    base_path = Path(OUTPUT_DIR)
    if not base_path.exists():
        app.logger.warning(f"Output directory '{OUTPUT_DIR}' not found.")
        return render_template('index.html', archives=[], error_message=f"Output directory '{OUTPUT_DIR}' not found.")

    for group_dir in sorted(base_path.iterdir()): # Sort group directories by name
        if group_dir.is_dir() and group_dir.name.startswith('group_'):
            group_id = group_dir.name.replace('group_', '')
            group_display_name = get_archive_display_name(group_id)

            # Check for complete_archive for the group
            complete_archive_path = group_dir / "complete_archive" / "archive.json"
            if complete_archive_path.exists():
                archived_items.append({
                    'type': 'group',
                    'id': group_id,
                    'name': group_display_name,
                    'group_id': group_id,
                    'topic_id': None # Indicates it's the whole group archive
                })

            # Check for topic archives within the group
            for item_in_group_dir in sorted(group_dir.iterdir()): # Sort topic directories by name
                if item_in_group_dir.is_dir() and item_in_group_dir.name.startswith('topic_'):
                    topic_id = item_in_group_dir.name.replace('topic_', '')
                    topic_archive_path = item_in_group_dir / "archive.json"
                    if topic_archive_path.exists():
                        topic_display_name = get_archive_display_name(group_id, topic_id)
                        archived_items.append({
                            'type': 'topic',
                            'id': f"{group_id}_{topic_id}", # Unique ID for routing
                            'name': topic_display_name,
                            'group_id': group_id,
                            'topic_id': topic_id
                        })
    
    # Sort all items by name for final display
    archived_items.sort(key=lambda x: x['name'])
    return render_template('index.html', archives=archived_items)

@app.route('/archive/group/<group_id_str>')
@app.route('/archive/group/<group_id_str>/topic/<topic_id_str>')
def chat_view(group_id_str, topic_id_str=None):
    try:
        messages_data = load_messages(group_id_str, topic_id_str)
        
        # Prepare authors list for dropdown filter
        authors = sorted(list(set(
            f"{msg.get('sender_first_name', '')} {msg.get('sender_last_name', '')}".strip() + 
            (f" (@{msg.get('sender_username')})" if msg.get('sender_username') else "")
            for msg in messages_data 
            if msg.get('sender_id') # Ensure there is a sender
        )))
        
        display_name = get_archive_display_name(group_id_str, topic_id_str)

        return render_template('chat.html', 
                             messages=messages_data, 
                             authors=authors, 
                             archive_display_name=display_name,
                             group_id=group_id_str,
                             topic_id=topic_id_str, # Can be None for whole group archive
                             output_dir=OUTPUT_DIR)
    except FileNotFoundError as e:
        app.logger.warning(f"Archive not found for group {group_id_str}, topic {topic_id_str}: {e}")
        return render_template("error.html", error_message=f"Archive not found. {str(e)}"), 404
    except Exception as e:
        app.logger.error(f"Error loading chat view for group {group_id_str} topic {topic_id_str}: {e}", exc_info=True)
        return render_template("error.html", error_message=f"An unexpected error occurred: {str(e)}"), 500

@app.route('/api/messages/group/<group_id_str>')
@app.route('/api/messages/group/<group_id_str>/topic/<topic_id_str>')
def api_messages(group_id_str, topic_id_str=None):
    try:
        messages_data = load_messages(group_id_str, topic_id_str)
        author_filter_str = request.args.get('author')
        
        if author_filter_str:
            # Normalize author filter string for comparison
            # Example author_filter_str: "FirstName LastName (@username)" or "FirstName LastName"
            # We need to match based on how authors are constructed for the dropdown
            
            filtered_messages = []
            for msg in messages_data:
                current_msg_author_str = f"{msg.get('sender_first_name', '')} {msg.get('sender_last_name', '')}".strip() + \
                                         (f" (@{msg.get('sender_username')})" if msg.get('sender_username') else "")
                if current_msg_author_str == author_filter_str:
                    filtered_messages.append(msg)
            messages_data = filtered_messages
        
        # Convert datetime objects to string for JSON serialization if they exist
        for msg in messages_data:
            if 'date_dt' in msg and isinstance(msg['date_dt'], datetime):
                msg['date'] = msg['date_dt'].strftime('%Y-%m-%d %H:%M:%S')
            elif 'date_str' in msg: # If original date was problematic
                msg['date'] = msg['date_str']

        # Remove date_dt and date_str as they're not directly JSON serializable by default jsonify
        for msg in messages_data:
            msg.pop('date_dt', None)
            msg.pop('date_str', None)

        return jsonify(messages_data)
    except FileNotFoundError as e:
        return jsonify({'error': f"Archive not found. {str(e)}"}), 404
    except Exception as e:
        app.logger.error(f"API error for group {group_id_str} topic {topic_id_str}: {e}", exc_info=True)
        return jsonify({'error': f"An unexpected error occurred: {str(e)}"}), 500

# Endpoint for serving media files
@app.route('/<string:output_dir_name>/group_<string:group_id_str>/topic_<string:topic_id_str>/media/<path:filename>')
@app.route('/<string:output_dir_name>/group_<string:group_id_str>/complete_archive/media/<path:filename>')
def serve_media(output_dir_name, group_id_str, filename, topic_id_str=None):
    try:
        if output_dir_name != Path(OUTPUT_DIR).name:
             # Basic security check if OUTPUT_DIR is complex, though less critical for local serving
            return "Invalid base directory for media.", 400

        if topic_id_str:
            media_file_path_base = Path(OUTPUT_DIR) / f"group_{group_id_str}" / f"topic_{topic_id_str}" / "media"
        else:
            media_file_path_base = Path(OUTPUT_DIR) / f"group_{group_id_str}" / "complete_archive" / "media"
        
        return send_from_directory(media_file_path_base, filename)
    except FileNotFoundError:
        app.logger.warning(f"Media file not found: {filename} in group {group_id_str}, topic {topic_id_str}")
        return "Media file not found.", 404
    except Exception as e:
        app.logger.error(f"Error serving media {filename} for group {group_id_str} topic {topic_id_str}: {e}", exc_info=True)
        return f"Error serving media: {str(e)}", 500

# Export functionality (simplified, might need adjustments for new structure)
@app.route('/export/group/<group_id_str>')
@app.route('/export/group/<group_id_str>/topic/<topic_id_str>')
def export_archive(group_id_str, topic_id_str=None):
    try:
        archive_name_part = f"group_{group_id_str}"
        if topic_id_str:
            archive_name_part += f"_topic_{topic_id_str}"
        else:
            archive_name_part += "_complete_archive"

        export_dir_base_name = f"export_{archive_name_part}"
        export_dir_path = Path(export_dir_base_name).resolve() # Use absolute path for export dir
        
        # Define source media directory
        if topic_id_str:
            source_archive_root_path = Path(OUTPUT_DIR) / f"group_{group_id_str}" / f"topic_{topic_id_str}"
        else:
            source_archive_root_path = Path(OUTPUT_DIR) / f"group_{group_id_str}" / "complete_archive"
        
        source_media_dir = source_archive_root_path / "media"

        if export_dir_path.exists():
            shutil.rmtree(export_dir_path) # Clean up old export if any
        os.makedirs(export_dir_path, exist_ok=True)
        
        # Copy media files
        destination_media_dir = export_dir_path / "media"
        if source_media_dir.exists() and source_media_dir.is_dir():
            shutil.copytree(source_media_dir, destination_media_dir, dirs_exist_ok=True)
        else:
            os.makedirs(destination_media_dir, exist_ok=True) # Create media dir even if no media
        
        messages_data = load_messages(group_id_str, topic_id_str)
        
        # For export, convert datetime objects to string representations
        for msg in messages_data:
            if 'date_dt' in msg and isinstance(msg['date_dt'], datetime):
                msg['date_for_html'] = msg['date_dt'].strftime('%Y-%m-%d %H:%M:%S')
            elif 'date_str' in msg:
                 msg['date_for_html'] = msg['date_str']
            else:
                 msg['date_for_html'] = "N/A"

        # Generate statistics
        total_messages = len(messages_data)
        unique_author_ids = set(msg.get('sender_id') for msg in messages_data if msg.get('sender_id'))
        unique_authors_count = len(unique_author_ids)
        media_count = sum(1 for msg in messages_data if msg.get('media_filename') and "skipped_large_file" not in msg.get('media_filename',''))
        
        archive_display_name_for_export = get_archive_display_name(group_id_str, topic_id_str)

        # Prepare data for embedding, ensuring datetime objects are removed
        messages_for_json_embedding = []
        for msg_orig in messages_data:
            msg_copy = msg_orig.copy()
            msg_copy.pop('date_dt', None)
            if 'date_str' in msg_copy and 'date' not in msg_copy: # Ensure 'date' field has the string date
                msg_copy['date'] = msg_copy['date_str']
            msg_copy.pop('date_str', None)
            # Ensure text_html is present for the export template
            if 'text_html' not in msg_copy:
                 html_text = convert_links_to_html(msg_copy.get('text', '')) 
                 msg_copy['text_html'] = format_telegram_style(html_text)
            messages_for_json_embedding.append(msg_copy)
            
        # This string will be directly embedded into a <script> tag.
        # It defines the messagesData variable for JavaScript.
        embedded_json_script = f"<script>\nconst messagesData = {json.dumps(messages_for_json_embedding, ensure_ascii=False, default=str)};\nconst messagesRawJson = {json.dumps(json.dumps(messages_for_json_embedding, ensure_ascii=False, default=str))}; /* For debugging */ \n</script>"

        # Javascript for authors (escaped for embedding in Python string)
        # This script assumes messagesData is already defined globally by embedded_json_script
        authors_script_content = (
            'const uniqueAuthors = [...new Set(messagesData.map(msg => { \n'
            '  const firstName = msg.sender_first_name || \'\'; \n'
            '  const lastName = msg.sender_last_name || \'\'; \n'
            '  const username = msg.sender_username ? `(@${msg.sender_username})` : \'\'; \n'
            '  return `${firstName} ${lastName}`.trim() + `${username}`; \n'
            '}).filter(name => name.trim()))].sort();'
        )
        # Full script tag for authors
        authors_full_script_tag = f"<script>{authors_script_content}</script>"

        html_content = render_template('chat_export.html', 
                                     archive_display_name=archive_display_name_for_export,
                                     group_id=group_id_str,
                                     topic_id=topic_id_str,
                                     total_messages_stat=total_messages,
                                     unique_authors_stat=unique_authors_count,
                                     media_count_stat=media_count,
                                     media_dir_name="media", 
                                     # Placeholder for where the main data script will be injected
                                     embedded_data_script_placeholder='<!-- EMBEDDED_DATA_SCRIPT_HERE -->',
                                     # Placeholder for where the authors script will be injected
                                     embedded_authors_script_placeholder='<!-- EMBEDDED_AUTHORS_SCRIPT_HERE -->'
                                     )
        
        # Inject the data script
        html_content = html_content.replace(
            '<!-- EMBEDDED_DATA_SCRIPT_HERE -->',
            embedded_json_script
        )
        # Inject the authors script
        html_content = html_content.replace(
            '<!-- EMBEDDED_AUTHORS_SCRIPT_HERE -->',
            authors_full_script_tag
        )

        with open(export_dir_path / "index.html", 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # make_archive base_name should be the full path to the zip without .zip
        # root_dir is the parent of the directory to be zipped
        # base_dir is the directory to be zipped
        shutil.make_archive(str(export_dir_path), 'zip', 
                            root_dir=export_dir_path.parent, 
                            base_dir=export_dir_path.name)
        
        final_zip_path = export_dir_path.with_suffix('.zip') # e.g., export_group_123.zip
        
        shutil.rmtree(export_dir_path)
        
        return send_file(final_zip_path, as_attachment=True, download_name=final_zip_path.name)
        
    except FileNotFoundError as e:
        app.logger.warning(f"Export failed: Archive not found for group {group_id_str}, topic {topic_id_str}: {e}")
        return render_template("error.html", error_message=f"Export failed: Archive not found. {str(e)}"), 404
    except Exception as e:
        app.logger.error(f"Error during export for group {group_id_str} topic {topic_id_str}: {e}", exc_info=True)
        return render_template("error.html", error_message=f"Error during export: {str(e)}"), 500

if __name__ == '__main__':
    # For development, you can set debug=True. For production, it's better to use a WSGI server.
    app.run(debug=False, host='0.0.0.0', port=5000) # Listen on all interfaces 