"""Small, shared display rules for the launcher and mod viewer."""
from . import core


def usable(plan):
    return plan is not None and bool(plan.mods) and len(plan.ready) == len(plan.mods)


def action(plan, busy=False, issue=None):
    if busy:
        return 'Working...', False
    if issue is not None or (plan is not None and plan.offline):
        return 'Retry', True
    if plan is None:
        return 'Check mods', True
    if not plan.changes:
        return 'All up to date', False
    if not plan.ready and all(change.action == 'install' for change in plan.changes):
        return 'Install mods', True
    return 'Update mods', True


def offline_message(plan):
    if usable(plan):
        return "Can't reach the mod site. Your installed mods match the last saved list and can still be used. Choose Retry to check for newer updates."
    if plan is not None:
        return "Can't reach the mod site. %d of %d required mods match the saved list. Reconnect and choose Retry to finish setup." % (len(plan.ready), len(plan.mods))
    return "Can't reach the mod site and no saved mod list is available. Reconnect and choose Retry to check your setup."


def friendly_error(error, plan=None):
    text = str(error)
    if isinstance(error, core.Offline):
        return offline_message(plan)
    if 'Recovery copies' in text:
        return 'Recovery is needed before launching. ' + text
    if 'Close A Township Tale' in text or 'game started' in text:
        return 'Close A Township Tale, then choose Retry. Mods can only be changed while the game is closed.'
    if 'MelonLoader' in text:
        return "Install MelonLoader with the launcher's Mods button, then start the game once. Choose Retry when its Mods folder is ready."
    if 'SHA-256' in text:
        return "A download couldn't be verified, so it wasn't installed. Choose Retry to download it again."
    if 'changed' in text or 'after the check' in text:
        return 'Your mod files changed while we were working. Choose Retry to check them again.'
    if 'invalid' in text or 'saved file list' in text:
        return "The mod list couldn't be read safely. Choose Retry. Details are in the launcher log."
    return "Couldn't finish this step. Choose Retry. Details are in the launcher log."


def next_launch(plan, sheikah, progress=None, issue=None, route_error=False):
    if progress:
        return progress, 'AMBER'
    if route_error:
        return 'Check the server address', 'RED'
    if sheikah is None:
        return 'Checking the selected server...', 'MUTED'
    if not sheikah:
        if plan is None and issue is not None:
            return 'Check Sheikah Mods before launching', 'AMBER'
        return 'Sheikah mods will be parked for this server', 'MUTED'
    if plan is not None and plan.offline:
        return ('Offline · Installed Sheikah mods usable' if usable(plan) else 'Offline · Reconnect to finish setup'), 'AMBER'
    if issue is not None:
        return 'Sheikah mods need attention · Retry', 'AMBER'
    if plan is None:
        return 'Checking Sheikah mods...', 'MUTED'
    if plan.changes:
        return ('Sheikah mods need installing' if action(plan)[0] == 'Install mods' else 'Sheikah mod updates available'), 'AMBER'
    return 'Ready for Sheikah', 'GREEN'
