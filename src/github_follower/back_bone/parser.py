import github_api as ga
import json
import asyncio

import threading
import datetime
from django.conf import settings
from django.utils.timezone import make_aware

import random

from asgiref.sync import sync_to_async

class Parser(threading.Thread):
    def __init__(self):
        # Initialize thread.
        super().__init__()

        # Set daemon to true.
        self.daemon = True

        self.global_token = None
        self.global_username = None

        self.retrieve_followers_task = None
        self.retrieve_and_save_task = None

    def run(self):
        print("Parser is running...")

        # Start the back-end parser.
        asyncio.run(self.work())

    @sync_to_async
    def get_users(self, gids):
        import gf.models as mdl

        return list(mdl.User.objects.all().exclude(gid__in = gids).order_by('seeded', 'last_parsed'))

    @sync_to_async
    def get_seed_users(self):
        import gf.models as mdl

        return list(mdl.Seeder.objects.all().select_related('user'))

    @sync_to_async
    def get_target_users(self):
        import gf.models as mdl

        return list(mdl.Target_User.objects.all().select_related('user'))

    @sync_to_async
    def get_setting(self, key):
        import gf.models as mdl

        val = mdl.Setting.get(key = key)

        return val

    @sync_to_async
    def get_filtered(self, otype, params = {}):
        if len(params) < 1:
            return list(otype.objects.all())
        else:
            return list(otype.objects.filter(**params))

    async def retrieve_and_save_followers(self, user):
        import gf.models as mdl

        # Ignore targeted users.
        targeted = True

        try:
            tmp = await self.get_filtered(mdl.Target_User, {"user": user})
            tmp = tmp[0]
        except Exception:
            targeted = False

        if targeted and tmp is None:
            targeted = False

        if targeted:
            return

        page = 1

        # Create a loop and go through.
        while True:
            # Make new connection got GitHub API and set user agent.
            api = ga.GH_API()

            # Authenticate globally.
            if self.global_username is not None and self.global_token is not None:
                api.authenticate(self.global_username, self.global_token)

            # Try sending request to GitHub API.
            try:
                await api.send("GET", '/users/' + user.username + '/followers?page=' + str(page))
            except Exception as e:
                print("[ERR] Failed to retrieve user's following list for " + user.username + " (request failure).")
                print(e)

                break

            # Retrieve response.
            try:
                resp = await api.retrieve_response()
            except Exception as e:
                print("[ERR] Failed to retrieve user's following list for " + user.username + " (response failure).")
                print(e)

                break  

            # Close connection.
            try:
                await api.close()
            except Exception as e:
                print("[ERR] HTTP close error.")
                print(e)

            # Decode JSON.
            try:
                data = json.loads(resp)
            except json.JSONDecodeError as e:
                print("[ERR] Failed to retrieve user's following list for " + self.username + " (JSON decode failure).")
                print(e)

                break

            # Make sure we have data, if not, break the loop.
            if len(data) < 1:
                break

            for nuser in data:
                if "id" not in nuser:
                    print("[ERR] ID field not found in JSON data.")

                    continue

                if "login" not in nuser:
                    print("[ERR] ID field not found in JSON data.")

                    continue

                # Check if user exists already.
                exists = True

                try:
                    new_user = await self.get_filtered(mdl.User, {"username": nuser["login"]})
                    new_user = new_user[0]
                except Exception as e:
                    exists = False

                if exists and new_user is None:
                    exists = False

                if not exists:
                    # Create new user by username.
                    new_user = mdl.User(gid = nuser["id"], username = nuser["login"], parent = user.gid, auto_added = True)

                    # Check if we're seeded.
                    seeded = True

                    try:
                        tmp = await self.get_filtered(mdl.Seeder, {"user": user})
                        tmp = tmp[0]
                    except Exception:
                        seeded = False

                    if seeded and tmp is None:
                        seeded = False

                    if seeded:
                        new_user.seeded = True

                    # Save user.
                    await sync_to_async(new_user.save)()

                    if bool(await self.get_setting("verbose")):
                        print("[V] Adding user " + nuser["login"] + " (parent " + user.username + ")")

            # Increment page
            page = page + 1
            
            await asyncio.sleep(float(random.randint(int(await self.get_setting("wait_time_list_min")), int(await self.get_setting("wait_time_list_max")))))

    async def loop_and_follow_targets(self, user):
        import gf.models as mdl

        # First, we should make sure we're following the target users.
        target_users = await self.get_target_users()

        for tuser in target_users:
            # Check if user exists already.
            exists = True

            try:
                fuser = await self.get_filtered(mdl.Following, {"target_user": tuser, "user": user})
                fuser = fuser[0]
            except Exception:
                exists = False

            if exists and fuser is None:
                exists = False

            # Check if we exist in the following list.
            if exists:
                continue

            fuser = mdl.Following(target_user = tuser, user = user)
            await sync_to_async(fuser.save)()

            # Follow target user.
            await tuser.follow_user(fuser.user)

            await asyncio.sleep(float(random.randint(int(await self.get_setting("wait_time_follow_min")), int(await self.get_setting("wait_time_follow_max")))))

    async def parse_user(self, user):
        if self.retrieve_and_save_task is None or self.retrieve_and_save_task.done():
            self.retrieve_and_save_task = asyncio.create_task(self.retrieve_and_save_followers(user))

        follow_targets_task = asyncio.create_task(self.loop_and_follow_targets(user))

        await asyncio.gather(follow_targets_task)

    async def retrieve_followers(self):
        import gf.models as mdl

        while True:
            tusers = await self.get_target_users()

            for user in tusers:
                # Use GitHub API.
                api = ga.GH_API()

                # Authenticate.
                api.authenticate(user.user.username, user.token)
                
                page = 1

                while True:
                    # Make connection.
                    try:
                        await api.send("GET", '/user/followers?page=' + str(page))
                    except Exception as e:
                        print("[ERR] Failed to retrieve target user's followers list for " + user.user.username + " (request failure).")
                        print(e)

                        break

                    # Retrieve results.
                    try:
                        resp = await api.retrieve_response()
                    except Exception as e:
                        print("[ERR] Failed to retrieve target user's followers list for " + user.user.username + " (response failure).")
                        print(e)

                        break

                    # Close connection.
                    try:
                        await api.close()
                    except Exception as e:
                        print("[ERR] HTTP close error.")
                        print(e)

                    # Decode JSON.
                    try:
                        data = json.loads(resp)
                    except json.JSONDecodeError as e:
                        print("[ERR] Failed to retrieve target user's followers list for " + user.user.username + " (JSON decode failure).")
                        print(e)

                        break

                    # Make sure we have data, if not, break the loop.
                    if len(data) < 1:
                        break

                    for fuser in data:
                        if "id" not in fuser:
                            continue

                        # Make sure user exists.
                        exists = True

                        muser = None

                        try:
                            muser = await self.get_filtered(mdl.User, {"gid": fuser["id"]})
                            muser = muser[0]
                        except Exception:
                            exists = False

                        if not exists or muser is None:
                            muser = mdl.User(gid = fuser["id"], username = fuser["login"])
                            await sync_to_async(muser.save)()

                        # Add to follower list if not already on it.
                        exists = True

                        try:
                            tmp = await self.get_filtered(mdl.Follower, {"target_user": user, "user": muser})
                            tmp = tmp[0]
                        except Exception:
                            exists = False

                        if exists and tmp is None:
                            exists = False

                        if exists:
                            continue

                        # Now add it to list and unfollow user.
                        new_user = mdl.Follower(target_user = user, user = muser)
                        await sync_to_async(new_user.save)()

                        await user.unfollow_user(muser)

                        await asyncio.sleep(float(random.randint(int(await self.get_setting("wait_time_follow_min")), int(await self.get_setting("wait_time_follow_max")))))

                    # Increment page
                    page = page + 1

                    await asyncio.sleep(float(random.randint(int(await self.get_setting("wait_time_list_min")), int(await self.get_setting("wait_time_list_max")))))
        
    async def parse_users(self):
        import gf.models as mdl

        # Retrieve users.
        seed_users = await self.get_seed_users()
        target_users = await self.get_target_users()
        max_users = int(await self.get_setting("max_scan_users"))

        # Loop for target GIDs to exclude from parsing list.
        gids = []

        for user in target_users:
            gids.append(user.user.gid)

        # Retrieve users excluding target users.
        users = await self.get_users(gids)

        for user in users[:max_users]:
            # Update last parsed.
            user.last_parsed = make_aware(datetime.datetime.now())

            # Check if seeded.
            if user.seeded:
                user.seeded = False

            # Update GUID.
            await user.retrieve_github_id()

            # Save user.
            await sync_to_async(user.save)()

            # Make sure this isn't a seed user and the amount of users we have isn't equal to seed users.
            seeder = True

            try:
                tmp = await self.get_filtered(mdl.Seeder, {"user": user})
                tmp = tmp[0]
            except Exception:
                seeder = False

            if seeder and tmp is None:
                seeder = False

            if seeder and len(seed_users) < len(users):
                continue

            # Parse user.
            await self.parse_user(user)

    async def work(self):
        # Retrieve all target users
        tusers = await self.get_target_users()

        # Set global username and token.s
        for user in tusers:
            if user.global_user:
                self.global_username = user.user.username
                self.global_token = user.token

        # Create a loop until the program ends.
        while True:
            asyncio.create_task(self.parse_users())

            if self.retrieve_followers_task is None or self.retrieve_followers_task.done():
                self.retrieve_followers_task = asyncio.create_task(self.retrieve_followers())

            # Wait whatever scan time is in seconds.
            await asyncio.sleep(float(random.randint(int(await self.get_setting("scan_time_min")), int(await self.get_setting("scan_time_max")))))

parser = Parser()