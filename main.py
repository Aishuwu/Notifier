import os
import re
import nextcord
from nextcord.ext import commands, tasks
from googleapiclient.discovery import build
from functools import partial

intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
if not YOUTUBE_API_KEY:
    print("Error: YOUTUBE_API_KEY is missing!")
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

tracked_channels = {}
last_live_streams = {}

# Helper function to extract channel ID or username from URL
def extract_channel_info_from_url(url):
    channel_match = re.match(r'.*youtube\.com/channel/([a-zA-Z0-9_-]{24})', url)
    custom_match = re.match(r'.*youtube\.com/(c|user)/([a-zA-Z0-9_-]+)', url)

    if channel_match:
        return channel_match.group(1)  # Channel ID found
    elif custom_match:
        return custom_match.group(2)  # Custom username or old user URL
    return None

# Function to search for a channel by display name (username or custom URL name)
def get_channel_id_from_search(display_name):
    request = youtube.search().list(
        part="snippet",
        q=display_name,
        type="channel",
        maxResults=1
    )
    response = request.execute()
    return response['items'][0]['id']['channelId'] if 'items' in response and len(response['items']) > 0 else None

# Function to determine whether input is a channel ID, username, or URL, and fetch the appropriate channel ID
def get_channel_id(input_str):
    if re.match(r'^UC[a-zA-Z0-9_-]{22}$', input_str):
        return input_str
    extracted_info = extract_channel_info_from_url(input_str)
    return extracted_info if extracted_info else get_channel_id_from_search(input_str)

# Function to fetch the name of a YouTube channel from its ID
def get_channel_name(channel_id):
    request = youtube.channels().list(part="snippet", id=channel_id)
    response = request.execute()
    return response['items'][0]['snippet']['title'] if 'items' in response and len(response['items']) > 0 else None

# Slash command to add a YouTube channel to track
async def add_channel(interaction, input_str):
    await interaction.response.defer()

    guild_id = interaction.guild.id
    channel_id = get_channel_id(input_str)

    if not channel_id:
        await interaction.followup.send(f"Error: Unable to find channel by the input '{input_str}'. Please check the channel ID, URL, or display name.")
        return

    tracked_channels.setdefault(guild_id, [])

    if channel_id not in tracked_channels[guild_id]:
        channel_name = get_channel_name(channel_id)
        if channel_name:
            tracked_channels[guild_id].append(channel_id)
            await interaction.followup.send(f"Now tracking YouTube channel: {channel_name}")
            print(f"Tracking channel {channel_name} (ID: {channel_id}) for guild {guild_id}")
        else:
            await interaction.followup.send("Error: Unable to retrieve the channel name. Please check the channel ID, URL, or display name.")
    else:
        await interaction.followup.send(f"Channel {channel_id} is already being tracked.")

# Slash command to remove a YouTube channel from tracking
async def remove_channel(interaction):
    await interaction.response.defer()

    guild_id = interaction.guild.id

    if guild_id not in tracked_channels or len(tracked_channels[guild_id]) == 0:
        await interaction.followup.send("No channels are currently being tracked.")
        return

    options = list(map(lambda channel_id: nextcord.SelectOption(
        label=get_channel_name(channel_id) or f"Unknown Channel (ID: {channel_id})", value=channel_id
    ), tracked_channels[guild_id]))

    class ChannelSelect(nextcord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="Select a channel to remove...", min_values=1, max_values=1, options=options)

        async def callback(self, interaction):
            selected_channel_id = self.values[0]
            tracked_channels[guild_id].remove(selected_channel_id)
            last_live_streams.pop(selected_channel_id, None)
            await interaction.followup.send(f"Removed YouTube channel: {get_channel_name(selected_channel_id) or 'Unknown Channel'}")
            print(f"Removed channel {selected_channel_id} for guild {guild_id}")

    view = nextcord.ui.View()
    view.add_item(ChannelSelect())
    await interaction.followup.send("Select a channel to remove:", view=view)

# Slash command to list all tracked channels for the guild
async def list_channels(interaction):
    await interaction.response.defer()

    guild_id = interaction.guild.id

    if guild_id in tracked_channels and len(tracked_channels[guild_id]) > 0:
        channels_list = "\n".join(map(lambda channel_id: get_channel_name(channel_id) or f"Unknown Channel (ID: {channel_id})", tracked_channels[guild_id]))
        await interaction.followup.send(f"Currently tracking these channels:\n{channels_list}")
    else:
        await interaction.followup.send("No channels are currently being tracked.")

# Function to check if any of the YouTube channels are live
def check_live_stream(channel_id):
    request = youtube.search().list(part="snippet", channelId=channel_id, eventType="live", type="video", maxResults=1)
    response = request.execute()

    if 'items' in response and len(response['items']) > 0:
        stream = response['items'][0]
        stream_title = stream['snippet']['title']
        stream_thumbnail = stream['snippet']['thumbnails']['high']['url']
        stream_url = f"https://www.youtube.com/watch?v={stream['id']['videoId']}"
        video_id = stream['id']['videoId']
        return True, stream_title, stream_thumbnail, stream_url, video_id
    return False, None, None, None, None

# Task to check for live streams periodically for multiple channels
@tasks.loop(minutes=3)
async def check_streams():
    print("Checking streams...")
    for guild_id, channels in tracked_channels.items():
        for channel_id in channels:
            print(f"Checking channel {channel_id} for guild {guild_id}")
            is_live, stream_title, stream_thumbnail, stream_url, video_id = check_live_stream(channel_id)

            if not is_live:
                last_live_streams[channel_id] = None
                continue

            if last_live_streams.get(channel_id) == video_id:
                continue

            guild = bot.get_guild(guild_id)
            if guild and is_live:
                last_live_streams[channel_id] = video_id
                embed = nextcord.Embed(
                    title=f"{stream_title} is live!",
                    description=f"[Click to watch the stream]({stream_url})",
                    color=nextcord.Color.red()
                )
                embed.set_image(url=stream_thumbnail)
                channel = guild.text_channels[0]
                await channel.send(content="@everyone", embed=embed)

# Register slash commands
bot.slash_command(name="add_channel", description="Add a YouTube channel (ID, URL, or display name) to track for live streams")(add_channel)
bot.slash_command(name="remove_channel", description="Remove a YouTube channel from tracking")(remove_channel)
bot.slash_command(name="list_channels", description="List all YouTube channels being tracked")(list_channels)

@bot.slash_command(name="ping", description="Ping the bot to check if it's online.")
async def ping(interaction):
    await interaction.response.send_message("Pong!")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.change_presence(activity=nextcord.Activity(type=nextcord.ActivityType.watching, name="YouTube streams"))
    try:
        await bot.sync_application_commands()
    except Exception as e:
        print(f"Error syncing slash commands: {e}")
    check_streams.start()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN is missing!")
else:
    bot.run(DISCORD_BOT_TOKEN)
