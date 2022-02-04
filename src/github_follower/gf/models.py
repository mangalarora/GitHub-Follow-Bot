from django.db import models
import json
import http.client

import back_bone

import asyncio

import github_api as ga

from asgiref.sync import sync_to_async

class Setting(models.Model):
    key = models.CharField(verbose_name = "Key", help_text="The setting key.", max_length = 64, unique = True)
    val = models.CharField(verbose_name = "Value", help_text="The setting value.", max_length = 64)

    def create(key, val, override):
        item = None

        exists = True

        try:
            item = Setting.objects.filter(key = key)[0]
        except Exception:
            exists = False

        if item is None:
            exists = False

        if exists:
            # Make sure we want to override.
            if not override:
                return 
        else:
            item = Setting(key = key)

        # Set value and save.
        item.val = val
        item.save()

    def get(key):
        val = None
        exists = True

        try:
            item = Setting.objects.filter(key = key)[0]
        except Exception:
            exists = False

        if exists and item is None:
            exists = False

        if exists:
            val  = str(item.val)

        return val

    def __str__(self):
        return self.key

class User(models.Model):
    gid = models.IntegerField(editable = False, null = True)
    parent = models.IntegerField(editable = False, default = 0, null = True)

    username = models.CharField(verbose_name = "Username", help_text = "The GitHub username.", max_length = 64, unique = True)

    last_parsed = models.DateTimeField(editable = False, auto_now_add = False, null = True)

    needs_to_seed = models.BooleanField(editable = False, default = False)
    auto_added = models.BooleanField(editable = False, default = False)

    cur_page = models.IntegerField(editable = False, default = 1)

    async def retrieve_github_id(self):
        if back_bone.parser.api is None:
            back_bone.parser.api = ga.GH_API()

        if back_bone.parser.global_token is None or back_bone.parser.global_username is None:
            return

        back_bone.parser.api.authenticate(back_bone.parser.global_username, back_bone.parser.global_token)

        # Send request.
        try:
            await back_bone.parser.api.send('GET', '/users/' + self.username)
        except Exception as e:
            print("[ERR] Failed to retrieve Github ID for user " + self.username + " (request failure).")
            print(e)

            await back_bone.parser.api.close()
            await back_bone.parser.do_fail()

            return

        # Read response.
        try:
            resp = await back_bone.parser.api.retrieve_response()
        except Exception as e:
            print("[ERR] Failed to retrieve GitHub ID for user " + self.username + " (response failure).")
            print(e)

            await back_bone.parser.api.close()
            await back_bone.parser.do_fail()

            return

        return_code = await back_bone.parser.api.retrieve_response_code()

        # Check status code.
        if return_code != 200 and return_code != 204:
            await back_bone.parser.do_fail()

            return

        # Close connection.
        await back_bone.parser.api.close()

        # Decode JSON.
        try:
            data = json.loads(resp)
        except json.JSONDecodeError as e:
            print("[ERR] Failed to retrieve GitHub ID for user " + self.username + " (JSON decode failure).")
            print(e)

            return

        # Store GitHub ID.
        if "id" in data:
            self.gid = int(data["id"])
        else:
            print("[ERR] Failed to retrieve GitHub ID for user " + self.username + " (ID doesn't exist in JSON data).")
            return 

    def save(self, *args, **kwargs):
        try:
            super().save(*args, **kwargs)
        except Exception as e:
            print("[ERR] Error saving user.")
            print(e)

            return

        asyncio.run(self.retrieve_github_id())

    def __str__(self):
        return self.username

class Target_User(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    remove_following = models.BooleanField(verbose_name = "Remove Following", help_text = "Whether to remove a user that follows from the following list.", default = True)
    cleanup_days = models.IntegerField(verbose_name = "Cleanup Days", help_text = "Automatically purges uses from the following list after this many days.")
    token = models.CharField(verbose_name = "Personal Token", help_text = "GitHub's personal token for authentication.", max_length=128)
    global_user = models.BooleanField(verbose_name = "Global User", help_text = "If true, this user's token and username will be used for authentication in general.", default=False)
    allow_follow = models.BooleanField(verbose_name = "Allow Following", help_text = "If true, this user will start following parsed users.", default = True)
    allow_unfollow = models.BooleanField(verbose_name = "Allow Unfollowing", help_text = "If true, the bot will unfollow users for this target user.", default = True)

    async def follow_user(self, user):
        # Check if we should follow.
        if not self.allow_follow:
            return

        # Make connection GitHub's API.
        if back_bone.parser.api is None:
            back_bone.parser.api = ga.GH_API()

        # Authenticate
        back_bone.parser.api.authenticate(self.user.username, self.token)

        # Send request.
        try:
            await back_bone.parser.api.send('PUT', '/user/following/' + user.username)
        except Exception as e:
            print("[ERR] Failed to follow GitHub user " + user.username + " for " + self.user.username + " (request failure).")
            print(e)

            await back_bone.parser.api.close()
            await back_bone.parser.do_fail()

            return

        return_code = await back_bone.parser.api.retrieve_response_code()

        # Close connection.
        await back_bone.parser.api.close()

        # Check status code.
        if return_code != 200 and return_code != 204:
            await back_bone.parser.do_fail()

            return

        # Save to following.
        new_following = Following(target_user = self, user = user)

        await sync_to_async(new_following.save)()

        if int(await sync_to_async(Setting.get)(key = "verbose")) >= 1:
            print("[V] Following user " + user.username + " for " + self.user.username + ".")

    async def unfollow_user(self, user):
        # Check if we should unfollow.
        if not self.allow_unfollow:
            return

        # Make connection GitHub's API.
        if back_bone.parser.api is None:
            back_bone.parser.api = ga.GH_API()

        # Authenticate
        back_bone.parser.api.authenticate(self.user.username, self.token)

        # Send request.
        try:
            await back_bone.parser.api.send('DELETE', '/user/following/' + user.username)
        except Exception as e:
            print("[ERR] Failed to unfollow GitHub user " + user.username + " for " + self.user.username + " (request failure).")
            print(e)

            await back_bone.parser.api.close()
            await back_bone.parser.do_fail()

            return

        return_code = await back_bone.parser.api.retrieve_response_code()

        # Close connection.
        await back_bone.parser.api.close()

        # Check status code.
        if return_code != 200 and return_code != 204:
            await back_bone.parser.do_fail()

            return

        # Set user as purged.
        following = None

        try:
            following = await sync_to_async(Following.objects.get)(target_user = self, user = user)
        except Exception:
            following = None

        if following is not None:
            following.purged = True

            # Save.
            await sync_to_async(following.save)()

        if int(await sync_to_async(Setting.get)(key = "verbose")) >= 2:
            print("[VV] Unfollowing user " + user.username + " from " + self.user.username + ".")

    class Meta:
        verbose_name = "Target User"

    def __str__(self):
        return self.user.username

class Follower(models.Model):
    target_user = models.ForeignKey(Target_User, on_delete = models.CASCADE)
    user = models.ForeignKey(User, on_delete = models.CASCADE)

    time_added = models.DateTimeField(editable = False, auto_now_add = True)

    def __str__(self):
        return self.user.username

class Following(models.Model):
    target_user = models.ForeignKey(Target_User, on_delete = models.CASCADE)
    user = models.ForeignKey(User, on_delete = models.CASCADE)
    purged = models.BooleanField(editable = False, default = False)

    time_added = models.DateTimeField(editable = False, auto_now_add = True)

    def __str__(self):
        return self.user.username

class Seeder(models.Model):
    user = models.ForeignKey(User, on_delete = models.CASCADE)

    def save(self, *args, **kwargs):
        # We need to seed user.
        self.user.needs_to_seed = True
    
        try:
            super().save(*args, **kwargs)
        except Exception as e:
            print("[ERR] Error saving seed user.")
            print(e)

            return

    def __str__(self):
        return self.user.username