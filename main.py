import os
import nextcord
from nextcord.ext import commands, tasks
from googleapiclient.discovery import build
import re

intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
if not YOUTUBE_API_KEY:
    print("Error: YOUTUBE_API_KEY is missing!")
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

tracked_channels = {}

def get_channel_id_from_username(username):
    try:
        request = youtube.channels().list(
            part="snippet",
            forUsername=username
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            channel_id = response['items'][0]['id']
            return channel_id
        else:
            return None
    except Exception as e:
        print(f"Error fetching channel ID from username: {e}")
        return None

def get_channel_id(input_str):
    if re.match(r'^UC[a-zA-Z0-9_-]{22}$', input_str):
        return input_str
    else:
        return get_channel_id_from_username(input_str)

def get_channel_name(channel_id):
    try:
        request = youtube.channels().list(
            part="snippet",
            id=channel_id
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            channel_name = response['items'][0]['snippet']['title']
            return channel_name
        else:
            return None
    except Exception as e:
        print(f"Error fetching channel name: {e}")
        return None

@bot.slash_command(name="add_channel", description="Add a YouTube channel (by ID or username) to track for live streams.")
async def add_channel(interaction: nextcord.Interaction, input_str: str):
    guild_id = interaction.guild.id
    channel_id = get_channel_id(input_str)

    if not channel_id:
        await interaction.response.send_message(f"Error: Unable to find channel by the input '{input_str}'. Please check the channel ID or username.")
        return

    if guild_id not in tracked_channels:
        tracked_channels[guild_id] = []

    if channel_id not in tracked_channels[guild_id]:
        channel_name = get_channel_name(channel_id)
        if channel_name:
            tracked_channels[guild_id].append(channel_id)
            await interaction.response.send_message(f"Now tracking YouTube channel: {channel_name}")
            print(f"Tracking channel {channel_name} (ID: {channel_id}) for guild {guild_id}")
        else:
            await interaction.response.send_message("Error: Unable to retrieve the channel name. Please check the channel ID or username.")
    else:
        await interaction.response.send_message(f"Channel {channel_id} is already being tracked.")

@bot.slash_command(name="remove_channel", description="Remove a YouTube channel from tracking.")
async def remove_channel(interaction: nextcord.Interaction):
    guild_id = interaction.guild.id

    if guild_id not in tracked_channels or len(tracked_channels[guild_id]) == 0:
        await interaction.response.send_message("No channels are currently being tracked.")
        return

    options = []
    for channel_id in tracked_channels[guild_id]:
        channel_name = get_channel_name(channel_id)
        if channel_name:
            options.append(nextcord.SelectOption(label=channel_name, value=channel_id))
        else:
            options.append(nextcord.SelectOption(label=f"Unknown Channel (ID: {channel_id})", value=channel_id))

    class ChannelSelect(nextcord.ui.Select):
        def __init__(self):
            super().__init__(
                placeholder="Select a channel to remove...",
                min_values=1,
                max_values=1,
                options=options
            )

        async def callback(self, interaction: nextcord.Interaction):
            selected_channel_id = self.values[0]
            tracked_channels[guild_id].remove(selected_channel_id)
            channel_name = get_channel_name(selected_channel_id)
            await interaction.response.send_message(f"Removed YouTube channel: {channel_name or 'Unknown Channel'}")
            print(f"Removed channel {channel_name or selected_channel_id} for guild {guild_id}")

    view = nextcord.ui.View()
    view.add_item(ChannelSelect())
    await interaction.response.send_message("Select a channel to remove:", view=view)

@bot.slash_command(name="list_channels", description="List all YouTube channels being tracked.")
async def list_channels(interaction: nextcord.Interaction):
    guild_id = interaction.guild.id

    if guild_id in tracked_channels and len(tracked_channels[guild_id]) > 0:
        channel_names = []
        for channel_id in tracked_channels[guild_id]:
            channel_name = get_channel_name(channel_id)
            if channel_name:
                channel_names.append(channel_name)
            else:
                channel_names.append(f"Unknown Channel (ID: {channel_id})")

        channels_list = "\n".join(channel_names)
        await interaction.response.send_message(f"Currently tracking these channels:\n{channels_list}")
    else:
        await interaction.response.send_message("No channels are currently being tracked.")

def check_live_stream(channel_id):
    try:
        request = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            eventType="live",
            type="video",
            maxResults=1
        )
        response = request.execute()
        print(f"Response from YouTube API for channel {channel_id}: {response}")
        if 'items' in response and len(response['items']) > 0:
            stream = response['items'][0]
            stream_title = stream['snippet']['title']
            stream_thumbnail = stream['snippet']['thumbnails']['high']['url']
            stream_url = f"https://www.youtube.com/watch?v={stream['id']['videoId']}"
            return (True, stream_title, stream_thumbnail, stream_url)
    except Exception as e:
        print(f"Error fetching live stream data: {e}")
    return (False, None, None, None)

@tasks.loop(minutes=3)
async def check_streams():
    print("Checking streams...")
    for guild_id, channels in tracked_channels.items():
        for channel_id in channels:
            print(f"Checking channel {channel_id} for guild {guild_id}")
            is_live, stream_title, stream_thumbnail, stream_url = check_live_stream(channel_id)

            guild = bot.get_guild(guild_id)
            if guild and is_live:
                print(f"Channel {channel_id} is live with title: {stream_title}")
                embed = nextcord.Embed(
                    title=f"{stream_title} is live!",
                    description=f"[Click to watch the stream]({stream_url})",
                    color=nextcord.Color.red()
                )
                embed.set_image(url=stream_thumbnail)
                channel = guild.text_channels[0]
                await channel.send(content="@everyone", embed=embed)

@bot.slash_command(name="ping", description="Ping the bot to check if it's online.")
async def ping(interaction: nextcord.Interaction):
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
