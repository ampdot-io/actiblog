# ActiBlog Development Guide

## Environment Setup & Commands
- Setup: `uv sync` - Install Python dependencies
- Run crawler: `python main.py` - Start Twitter image crawler application
- Hugo server: `hugo server -D` - Run Hugo server with drafts enabled
- Generate site: `hugo` - Build static site into public/ directory

## Code Style Guidelines
- Python 3.11+ with type annotations
- Import order: stdlib, then third-party, then local imports
- Class naming: CamelCase (e.g., TwitterCrawlStatus)
- Function/variable naming: snake_case (e.g., crawl_user)
- Use f-strings for string formatting
- Error handling: Use try/except blocks with specific exceptions
- Async/await for I/O operations
- Document classes and functions with docstrings as needed

## Project Structure
- inputs/ - Input data files (e.g., twitter_usernames.json)
- intermediates/ - Downloaded data and processed files (created at runtime, gitignored)
- content/ - Manual Hugo blog posts
- data/ - Data for Hugo site generation (target for final processed outputs)

## SocialData API Integration
- API Key: Use environment variable `SOCIALDATA_API_KEY` for authentication
- Twitter Search: `GET /twitter/search?query={query}` - Returns tweets matching query
- Tweet Endpoints: Get tweets, replies, quotes, threads, media, and more
- Useful Endpoint Examples:
  - `GET /twitter/search?query=from:username` - Get user's tweets
  - `GET /twitter/user/{username}/tweets` - Get user's timeline
  - `GET /twitter/tweets/{id}` - Get specific tweet
  - `GET /twitter/tweets/{id}/comments` - Get comments on tweet
- Tweet Structure: Full tweet objects contain these key fields:
  - `id_str` - Tweet ID
  - `full_text` - Tweet text content
  - `entities.media` - Contains media items with media_url_https
  - `entities.user_mentions` - @mentions in the tweet
  - `user` - Full user object of tweet author
- Media: URLs are in `media_url_https` field in entities.media array
- Operators: Supports Twitter search operators (from:user, filter:media, etc.)
- Pagination: Use `next_cursor` parameter for additional results pages