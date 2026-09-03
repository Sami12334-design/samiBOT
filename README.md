# samiBOT FINAL SEARCH V4

This build keeps the existing bot features and fixes the YouTube `shutil` runtime error.

## Search improvements

1. **Public discovery without `SearchGlobalRequest`**
   - Uses public web search (`site:t.me`) to discover public Telegram usernames.
   - Resolves those usernames with Telethon and searches their public messages.
   - This can find public channels/groups the account has not joined.
   - It cannot find private/unindexed Telegram content.

2. **Three search scopes**
   - Public + Joined
   - Public only
   - Joined only

3. **Better result controls**
   - Media/type filters
   - Relevance or newest sorting
   - Pagination
   - Recent-search history
   - Deduplication and concurrency limits

## YouTube

- Direct yt-dlp remains primary.
- Deno is installed by `render-build.sh` for current YouTube JS challenges.
- Piped remains a last-resort fallback and is dynamically discovered from TeamPiped documentation.
- Individual Piped instances are not hard-coded.

YouTube can still change its anti-bot/PO-token requirements; no downloader can honestly guarantee 100% success forever.
