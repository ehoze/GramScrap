# Telegram Scraper & Web Viewer

Project to scrape messages from a specific topic or an entire group on Telegram and view them in a simple web interface.

## Features

*   Download messages from a selected topic (thread) or an entire group on Telegram.
*   Download media (images, videos, files) associated with messages.
*   Save data to JSON and Excel files.
*   Web interface (Flask) for browsing archived messages.
*   Filter messages by author.
*   Theme switching (light/dark).
*   Export topic/group content to a standalone HTML file (with embedded media and data).

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/ehoze/GramScrap.git
    cd REPOSITORY_NAME
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```bash
    python -m venv venv
    # On Windows
    venv\Scripts\activate
    # On macOS/Linux
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

This script requires several environment variables to be set for Telegram API access and script operation.

1.  **Telegram API Credentials:**
    *   `TELEGRAM_API_ID`: Your Telegram API ID.
    *   `TELEGRAM_API_HASH`: Your Telegram API Hash.
    *   `TELEGRAM_PHONE`: Your phone number (with country code, e.g., +12345678900).

    You can obtain your API ID and Hash from [https://my.telegram.org](https://my.telegram.org) under "API development tools".

2.  **Default Group ID (Optional):**
    *   `TELEGRAM_DEFAULT_GROUP_ID`: The default Telegram Group/Channel ID (e.g., -100XXXXXXXXXX) to scrape if no `--group_id` is provided via command line. This is useful if you primarily work with one group.

**How to set environment variables:**

*   **Temporarily (for the current session):**
    *   Windows (Command Prompt):
        ```cmd
        set TELEGRAM_API_ID=your_api_id
        set TELEGRAM_API_HASH=your_api_hash
        set TELEGRAM_PHONE=your_phone
        set TELEGRAM_DEFAULT_GROUP_ID=your_default_group_id
        ```
    *   Windows (PowerShell):
        ```powershell
        $env:TELEGRAM_API_ID="your_api_id"
        $env:TELEGRAM_API_HASH="your_api_hash"
        $env:TELEGRAM_PHONE="your_phone"
        $env:TELEGRAM_DEFAULT_GROUP_ID="your_default_group_id"
        ```
    *   macOS/Linux (Bash/Zsh):
        ```bash
        export TELEGRAM_API_ID="your_api_id"
        export TELEGRAM_API_HASH="your_api_hash"
        export TELEGRAM_PHONE="your_phone"
        export TELEGRAM_DEFAULT_GROUP_ID="your_default_group_id"
        ```
*   **Permanently:** Add these export/set commands to your shell's startup file (e.g., `.bashrc`, `.zshrc`, or via System Properties on Windows).
*   **Using a `.env` file (Recommended for development):**
    You can create a `.env` file in the project root and use a library like `python-dotenv` to load them. However, the current script directly uses `os.environ.get()`. If you use a `.env` file, ensure it's loaded by your environment or modify the script.
    Example `.env` file (ensure this file is in your `.gitignore`):
    ```
    TELEGRAM_API_ID=your_api_id
    TELEGRAM_API_HASH=your_api_hash
    TELEGRAM_PHONE=your_phone
    TELEGRAM_DEFAULT_GROUP_ID=your_default_group_id
    ```

## Usage

### 1. Scraping Data (`tgscrap.py`)

Run the `tgscrap.py` script from your terminal.

**Command Line Arguments:**

*   `--group_id <ID>`: (Optional if `TELEGRAM_DEFAULT_GROUP_ID` is set) The target Group or Channel ID (e.g., -100XXXXXXXXXX).
*   `--topic_id <ID>`: (Optional) The specific Topic ID within the group to scrape. If omitted, the entire group/channel specified by `--group_id` (or the default) will be scraped.

**Examples:**

*   **Scrape a specific topic in a group:**
    ```bash
    python tgscrap.py --group_id -100XXXXXXXXXX --topic_id 12345
    ```
    (This assumes `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` are set as environment variables.)

*   **Scrape an entire group/channel:**
    ```bash
    python tgscrap.py --group_id -100YYYYYYYYYY
    ```

*   **Scrape the default group (if `TELEGRAM_DEFAULT_GROUP_ID` is set):**
    ```bash
    python tgscrap.py
    ```
    (This will scrape the entire default group. Add `--topic_id` to scrape a specific topic within the default group).

The script will create an `output` directory. Inside, it will structure data as follows:
`output/group_<GROUP_ID>/topic_<TOPIC_ID>/` for specific topics.
`output/group_<GROUP_ID>/complete_archive/` for entire group archives.

Each archive directory will contain:
*   `archive.json`: All messages and metadata in JSON format.
*   `archive.xlsx`: All messages in Excel format.
*   `media/`: A subdirectory containing downloaded media files.

### 2. Viewing the Archive (`app.py`)

Run the Flask web application:

```bash
python app.py
```

Open your web browser and go to `http://127.0.0.1:5000` (or the address shown in the terminal, usually `http://0.0.0.0:5000` which means it's accessible on your local network).

The web interface will list all scraped groups and topics. You can browse messages, filter by author, and switch themes.

**Note on Topic Names in Web Interface:**
The file `app.py` contains a dictionary `TOPIC_NAMES` that maps group IDs and topic IDs to human-readable names. You might need to customize this dictionary if you scrape different groups or topics, or implement a more dynamic way to fetch topic names if desired.

Example structure in `app.py`:
```python
TOPIC_NAMES = {
    '-100GROUPID1': { 
        'DEFAULT_NAME': 'My Main Group Archive',
        '123': 'General Discussion',
        '456': 'Project Alpha'
    },
    '-100GROUPID2': {
        'DEFAULT_NAME': 'Another Group Full Archive',
        '789': 'Cool Stuff'
    }
}
```

## Data Structure (`archive.json`)

The `archive.json` file contains a JSON array of message objects. Each message object has the following structure:

```json
[
  {
    "id": 12345, // Integer: Message ID
    "date": "2023-10-26T10:30:00+00:00", // String: ISO 8601 date-time
    "sender_id": 987654321, // Integer: Sender's User ID
    "sender_username": "john_doe", // String or null: Sender's username
    "sender_first_name": "John", // String or null: Sender's first name
    "sender_last_name": "Doe", // String or null: Sender's last name
    "text": "This is a sample message. With a link: https://example.com", // String: Message text content
    "has_media": true, // Boolean: True if the message has media
    "media_filename": "12345_1698316200000_image.jpg", // String or null: Filename of the downloaded media (if any)
                                                         // Can also be "skipped_large_file_(SIZE_MB)MB"
    "has_links": true, // Boolean: Basic check if 'http' is in text
    "reply_to_message_id": 12340 // Integer or null: ID of the message this is a reply to
  }
  // ... more message objects
]
```

## Author

This project was created by Eryk Kucharski (ehoze).
GitHub: [https://github.com/ehoze](https://github.com/ehoze)

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue.

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details. 
