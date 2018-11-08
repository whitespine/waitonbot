import asyncio
from typing import List, Any, Match, AsyncGenerator

from slackclient import SlackClient  # Obvious

import channel_util
import identifier
import job_nagger
import job_signoff
import management_commands
import scroll_util
import slack_util
import slavestothemachine
from dummy import FakeClient

# Read api token from file
api_file = open("apitoken.txt", 'r')
SLACK_API = next(api_file).strip()
api_file.close()

# Enable to use dummy
DEBUG_MODE = False


def main() -> None:
    wrap = ClientWrapper()

    # Add scroll handling
    wrap.add_hook(scroll_util.scroll_hook)

    # Add id handling
    wrap.add_hook(identifier.check_hook)
    wrap.add_hook(identifier.identify_hook)
    wrap.add_hook(identifier.identify_other_hook)
    wrap.add_hook(identifier.name_hook)

    # Added channel utility
    wrap.add_hook(channel_util.channel_check_hook)

    # Add nagging functionality
    wrap.add_hook(job_nagger.nag_hook)

    # Add kill switch
    wrap.add_hook(management_commands.reboot_hook)

    # Add towel rolling
    wrap.add_hook(slavestothemachine.count_work_hook)
    wrap.add_hook(slavestothemachine.dump_work_hook)

    # Add signoffs
    wrap.add_hook(job_signoff.signoff_hook)
    wrap.add_hook(job_signoff.undosignoff_hook)
    wrap.add_hook(job_signoff.reset_hook)

    # Add help
    help_callback = management_commands.list_hooks_callback_gen(wrap.hooks)
    wrap.add_hook(slack_util.Hook(help_callback, pattern=management_commands.bot_help_pattern))

    # Schedule?
    async def do_later(slk: SlackClient, msg: dict, match: Match):
        time = int(match.group(1))
        await asyncio.sleep(time)
        slack_util.reply(slk, msg, "hello!")

    wrap.add_hook(slack_util.Hook(do_later, "pingme\s+(\d+)"))

    event_loop = asyncio.get_event_loop()
    event_loop.run_until_complete(wrap.listen())


class ClientWrapper(object):
    """
    Essentially the main state object.
    We only ever expect one of these.
    Holds a slack client, and handles messsages.
    """

    def __init__(self):
        # Init slack
        if DEBUG_MODE:
            self.slack = FakeClient()
        else:
            self.slack = SlackClient(SLACK_API)

        # For overriding output channel
        self.debug_slack = slack_util.SlackDebugCondom(self.slack)

        # Hooks go regex -> callback on (slack, msg, match)
        self.hooks: List[slack_util.Hook] = []

    def add_hook(self, hook: slack_util.Hook) -> None:
        self.hooks.append(hook)

    async def listen(self):
        async for _ in self.spool_tasks():
            print("Handling a message...!")

    async def spool_tasks(self) -> AsyncGenerator[asyncio.Task, Any]:
        async for msg in self.async_message_feed():
            # Preprocess msg
            # We only care about standard messages, not subtypes, as those usually just channel activity
            if msg.get("subtype") is not None:
                continue

            # Never deal with general, EVER!
            if msg.get("channel") == channel_util.GENERAL:
                continue

            # Strip garbage
            msg['text'] = msg['text'].strip()

            # Handle debug
            if msg['text'][:6] == "DEBUG ":
                slack_to_use = self.debug_slack
                msg['text'] = msg['text'][6:]
                print("Debug handling \"{}\"".format(msg['text']))
            else:
                slack_to_use = self.slack

            # Msg is good
            # Find which hook, if any, satisfies
            sat_hook = None
            sat_match = None
            for hook in self.hooks:
                match = hook.check(msg)
                if match is not None:
                    sat_match = match
                    sat_hook = hook
                    break

            # If no hooks, continue
            if not sat_hook:
                continue

            # Throw up as a task, otherwise
            coro = sat_hook.invoke(slack_to_use, msg, sat_match)
            task = asyncio.create_task(coro)
            yield task

    async def async_message_feed(self) -> AsyncGenerator[dict, None]:
        """
        Async wrapper around the message feed.
        Yields messages awaitably forever.
        """
        # Create the msg feed
        feed = slack_util.message_stream(self.slack)

        # Create a simple callable that gets one message from the feed
        def get_one():
            return next(feed)

        # Continuously yield async threaded tasks that poll the feed
        while True:
            yield await asyncio.get_running_loop().run_in_executor(None, get_one)


# run main
if __name__ == '__main__':
    main()
