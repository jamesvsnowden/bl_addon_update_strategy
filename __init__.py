
from typing import Any, Dict, Optional, Protocol, Set, TYPE_CHECKING, Union
from contextlib import suppress
import datetime
import zipfile
import os
import threading
import urllib
import urllib.request
import bpy
import addon_utils
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
if TYPE_CHECKING:
    from bpy.types import Context, Event, Preferences, Text
    class AddonModule(Protocol):
        __name__: str
        __file__: str
        bl_info: Dict[str, Any]

_update_module = ""
_update_server = ""
_update_script = ''' 
import bpy
import addon_utils
import os
import shutil
import pathlib
import zipfile
import tempfile

PROPS = [
    ("check_for_updates_at_startup", False),
    ("include_beta_versions", False),
    ("api_token", ""),
    ("include_unstable", False)
    ]


def set_error(prefs, error, reenable=False, reinstall=None):
    print(error)
    prefs.update_status = "ERROR"
    prefs.update_error = str(error)
    if reinstall:
        try:
            addon_utils.disable("<ADDON>", default_set=True)
            shutil.rmtree(reinstall[0])
            addon_utils.modules_refresh()
            bpy.ops.preferences.addon_install(filepath=reinstall[1])
            bpy.ops.preferences.addon_enable(module="<ADDON>")
        except Exception as error:
            msg = "A backup of the addon was created at " + reinstall[1]
            def draw_func(self, _):
                layout = self.layout
                layout.separator()
                layout.label(icon="BLANK1", text="An unexpected error occurred. See console for  details")
                layout.label(icon="BLANK1", text=msg)
                layout.label(icon="BLANK1", text="Please reinstall the addon manually.")
                layout.separator()
            bpy.context.window_manager.popup_menu(draw_func,
                                                  title="Reinstallation Failed",
                                                  icon="ERROR")
    elif reenable:
        try:
            addon_utils.enable(module="<ADDON>")
        except: pass


def make_backup(path):
    srcpath = pathlib.Path(path).expanduser().resolve(strict=True)
    dirpath = tempfile.mkdtemp()
    zippath = os.path.join(dirpath, "<ADDON>_backup.zip")

    with zipfile.ZipFile(zippath, "w", zipfile.ZIP_DEFLATED) as file:
        for item in srcpath.rglob("*.py"):
            file.write(item, item.relative_to(srcpath.parent))

    return zippath


def install_update():
    prefs = bpy.context.preferences.addons["<ADDON>"].preferences
    props = [(key, prefs.get(key, default)) for key, default in PROPS]
    prefs = None

    path = ""
    for item in addon_utils.modules():
        if item.__name__ == "<ADDON>" and os.path.exists(item.__file__):
            path = os.path.dirname(item.__file__)

    if not path:
        return set_error(prefs, "Failed to find addon directory")

    backup_path = ""
    try:
        backup_path = make_backup(path)
    except Exception as error:
        return set_error(prefs, error)

    try:
        addon_utils.disable("<ADDON>", default_set=True)
    except Exception as error:
        return set_error(prefs, error, reenable=True)

    try:
        shutil.rmtree(path)
    except Exception as error:
        return set_error(prefs, error, reenable=True)

    try:
        addon_utils.modules_refresh()
    except Exception as error:
        return set_error(prefs, error, reinstall=(path, backup_path))

    try:
        bpy.ops.preferences.addon_install(filepath=r"<FILEPATH>")
    except Exception as error:
        return set_error(prefs, error, reinstall=(path, backup_path))

    try:
        bpy.ops.preferences.addon_enable(module="<ADDON>")
    except Exception as error:
        return set_error(prefs, error, reinstall=(path, backup_path))
    else:
        prefs = bpy.context.preferences.addons["<ADDON>"].preferences
        prefs["version"] = prefs.get("new_release_version", ")
        prefs["new_release_version"] = ""
        prefs["new_release_url"] = ""
        prefs["new_release_date"] = ""
        prefs["new_release_path"] = ""
        prefs["update_error"] = ""
        prefs["update_status"] = 0
        for key, value in props:
            prefs[key] = value
        bpy.ops.preferences.addon_expand(module="<ADDON>")

if __name__ == "__main__":
    bpy.app.timers.register(install_update, first_interval=1)

'''


def get_preferences(context: Optional['Context']=None) -> Optional['Preferences']:
    with suppress(Exception):
        context = bpy.context if context is None else context
        return context.preferences


def get_addon_preferences(context: Optional['Context']=None) -> Optional['AddonUpdatePreferences']:
    with suppress(Exception):
        return get_preferences(context).addons[_update_module].preferences


def get_addon_module() -> Optional['AddonModule']:
    return next((mod for mod in addon_utils.modules() if mod.__name__ == _update_module), None)


def get_addon_info(default: Optional[Dict[str, Any]]=None) -> Optional[Dict[str, Any]]:
    mod = get_addon_module()
    return mod.bl_info if mod is not None else default


def get_addon_info_value(key: str, default: Optional[Any]=None) -> Any:
    return get_addon_info({}).get(key, default)


def validate_version_tuple(version: Any) -> bool:
    return (isinstance(version, (tuple, list))
            and len(version) == 3
            and all(isinstance(element, int) for element in version))


def update_filepath_check(filepath: str) -> Optional[str]:
    if not os.path.exists(filepath):
        return"Invalid update file path"

    if not zipfile.is_zipfile(filepath):
        return "Invalid update file type"


def _cancel_with_error(op: Operator,
                       prefs: 'AddonUpdatePreferences',
                       error: Union[Exception, str]) -> Set[str]:
    prefs.update_status = 'ERROR'
    prefs.update_error = str(error)
    op.report({'ERROR'}, error)
    return {'CANCELLED'}


def _send_update_check_request(op: 'AddonUpdateCheck', url: str) -> None:
    import json, urllib, urllib.request
    try:
        resp = urllib.request.urlopen(url)
        data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as err:
        op._result = err
    else:
        op._result = data if isinstance(data, dict) else {"url": data}
    finally:
        if not isinstance(op._result, (Exception, dict)):
            op._result = RuntimeError("Unknown error. Contact addon maintainer")


def _send_update_download_request(op: 'AddonUpdateDownload', url: str) -> None:
    import urllib, urllib.request
    try:
        path, _ = urllib.request.urlretrieve(url)
    except (urllib.error.URLError, urllib.error.HTTPError) as err:
        op._result = err
    else:
        op._result = path
    finally:
        if not isinstance(op._result, (Exception, str)):
            op._result = RuntimeError("Unknown error. Contact addon maintainer")


def _get_or_create_update_script_text() -> 'Text':
    name = f'{_update_module}_update_script'
    text = bpy.data.texts.get(name)
    if text:
        text.clear()
    else:
        text = bpy.data.texts.new(name)
    return text


class AddonUpdateCheck(Operator):
    bl_idname = ""
    bl_label = "Check for Update"
    bl_description = "Check if an update is available"
    bl_options = {'INTERNAL'}

    _timer = None
    _thread = None
    _result = None

    @classmethod
    def poll(cls, context: 'Context') -> bool:
        prefs = get_addon_preferences(context)
        return prefs is not None and bool(prefs.api_token)

    def modal(self, context: 'Context', event: 'Event') -> Set[str]:
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        prefs = get_addon_preferences()

        area = context.area
        if area:
            area.tag_redraw()

        res = self._result
        if res is None:
            prefs.update_progress = self._timer.time_duration
            return {'PASS_THROUGH'}

        self._thread.join()
        self._thread = None
        self._result = None
        self.cancel(context)

        if isinstance(res, Exception):
            prefs.update_status = 'ERROR'
            prefs.update_error = str(res)
            return {'CANCELLED'}

        if not isinstance(res, dict) or not res.get("url") or not isinstance(res["url"], str):
            prefs.update_status = 'NO_UPDATE'
            return {'CANCELLED'}

        prefs.new_release_date = res.get("date", "")
        prefs.new_release_notes = res.get("notes", "")
        prefs.new_release_url = res["url"]
        prefs.new_release_version = res.get("version", "")
        prefs.new_release_warning = res.get("warning", "")
        prefs.update_status = 'AVAILABLE'

        return {'FINISHED'}

    def execute(self, context: 'Context') -> Set[str]:
        prefs = get_addon_preferences(context)

        if prefs is None:
            self.report({'ERROR'}, "Unable to find addon preferences")
            return {'CANCELLED'}

        if not isinstance(prefs, AddonUpdatePreferences):
            self.report({'ERROR'}, "Invalid preferences. Contact addon maintainer")
            return {'CANCELLED'}

        version = get_version(prefs)
        if not version:
            return _cancel_with_error(self, prefs, "Invalid bl_info.version. Contact addon maintainer")

        if not _update_server:
            return _cancel_with_error(self, prefs, "Update server URL not found. Contact addon maintainer.")

        params = {
            "blender_version": ".".join(map(str, bpy.app.version)),
            "addon_name": _update_module,
            "addon_version": version,
            "api_token": prefs.api_token,
            "include_unstable": prefs.include_unstable
            }

        prefs.update_status = 'CHECKING'
        prefs.update_progress = 0.0

        area = context.area
        if area:
            area.tag_redraw()

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        self._result = None
        self._thread = threading.Thread(target=_send_update_check_request,
                                        args=(self, f'{_update_server}?{urllib.parse.urlencode(params)}'))
        self._thread.start()

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context: 'Context') -> None:
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None

        prefs = get_addon_preferences()
        if prefs:
            prefs.update_progress = 0.0


class AddonUpdateReset(Operator):
    bl_idname = ""
    bl_label = "OK"
    bl_description = "Acknowledge"
    bl_options = {'INTERNAL'}

    def execute(self, context: 'Context') -> Set[str]:
        prefs = get_addon_preferences(context)
        if prefs:
            prefs.new_release_date = ""
            prefs.new_release_notes = ""
            prefs.new_release_path = ""
            prefs.new_release_url = ""
            prefs.new_release_version = ""
            prefs.new_release_warning = ""
            prefs.update_error = ""
            prefs.update_status = 'NONE'

        return {'FINISHED'}


class AddonUpdateDownload(Operator):
    bl_idname = ""
    bl_label = "Download"
    bl_description = "Download update"
    bl_options = {'INTERNAL'}

    _timer = None
    _thread = None
    _result = None

    @classmethod
    def poll(cls, context: 'Context') -> bool:
        prefs = get_addon_preferences(context)
        return prefs is not None and prefs.update_status == 'AVAILABLE'

    def modal(self, context: 'Context', event: 'Event') -> Set[str]:
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        prefs = get_addon_preferences(context)
        prefs.update_progress = self._timer.time_duration

        area = context.area
        if area:
            area.tag_redraw()

        res = self._result
        if res is None:
            return {'PASS_THROUGH'}

        self._thread.join()
        self._thread = None
        self._result = None
        self.cancel(context)

        if isinstance(res, Exception):
            prefs.update_status = 'ERROR'
            prefs.update_error = str(res)
            self.report({'ERROR'}, str(res))
            return {'CANCELLED'}

        prefs.update_status = 'READY'
        prefs.new_release_path = res
        return {'FINISHED'}

    def execute(self, context: 'Context') -> Set[str]:

        prefs = get_addon_preferences(context)

        if prefs is None:
            self.report({'ERROR'}, "Unable to find addon preferences")
            return {'CANCELLED'}

        url = prefs.new_release_url
        if not url:
            return _cancel_with_error(self, prefs, "Invalid download URL")

        prefs.update_status = 'DOWNLOADING'
        prefs.update_progress = 0.0

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        self._result = None
        self._thread = threading.Thread(target=_send_update_download_request, args=(self, url))
        self._thread.start()

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context: 'Context') -> None:
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None

        prefs = get_addon_preferences(context)
        if prefs:
            prefs.update_progress = 0.0


class AddonUpdateInstall(Operator):
    bl_idname = ""
    bl_label = "Install"
    bl_description = "Install update"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context: 'Context') -> bool:
        prefs = get_addon_preferences(context)
        return isinstance(prefs, AddonUpdatePreferences) and prefs.update_status == 'READY'

    def execute(self, context: 'Context') -> Set[str]:

        prefs = get_addon_preferences(context)
        if prefs is None:
            self.report({'ERROR'}, "Unable to find addon preferences")
            return {'CANCELLED'}

        path = prefs.new_release_path

        err = update_filepath_check(path)
        if err:
            return _cancel_with_error(self, prefs, err)

        text = _get_or_create_update_script_text()
        text.write(_update_script.replace("<ADDON>", _update_module).replace("<FILEPATH>", path))

        try:
            context = context.copy()
            context["edit_text"] = text
            bpy.ops.text.run_script(context)
        except Exception as err:
            return _cancel_with_error(err)

        return {'FINISHED'}
        

class AddonUpdatePreferencesOpen(Operator):
    bl_idname = ""
    bl_label = "Open Preferences"
    bl_description = "Open the addon preferences window"
    bl_options = {'INTERNAL'}

    def execute(self, context: 'Context') -> Set[str]:
        prefs = get_preferences(context)
        if prefs is None:
            self.report({'ERROR'}, "Failed to access Blender preferences")
            return {'CANCELLED'}

        addon_prefs = get_addon_preferences(context)
        if addon_prefs is None:
            self.report({'ERROR'}, "Failed to access addon preferences")
            return {'CANCELLED'}

        bpy.ops.sceen.userpref_show('INVOKE_DEFAULT')
        prefs.active_section = 'ADDONS'
        bpy.ops.preferences.addon_show(module=_update_module)
        bpy.ops.preferences.addon_expand(module=_update_module)

        return {'FINISHED'}


def get_version(prefs: 'AddonUpdatePreferences') -> str:
    value = prefs.get("version", "")
    if not value:
        elements = get_addon_info_value("version")
        if validate_version_tuple(elements):
            value = ".".join(elements)
    return value


class AddonUpdatePreferences:

    api_token: StringProperty(
        name="API Token",
        description="API token for auto-update",
        default="",
        options=set()
        )

    check_for_updates_on_startup: BoolProperty(
        name="Check on startup",
        description="Automatically check for updates on startup",
        default=False,
        options=set()
        )

    include_unstable: BoolProperty(
        name="Include Unstable",
        description="Include unstable versions when checking for updates",
        default=False,
        options=set()
        )

    new_release_date: StringProperty(
        name="Date",
        description="Release date (optional)",
        default="",
        options={'HIDDEN'}
        )

    new_release_notes: StringProperty(
        name="Notes",
        description="Release notes URL (optional)",
        default="",
        options={'HIDDEN'}
        )

    new_release_path: StringProperty(
        name="Path",
        description="Local path to downloaded zip file",
        default="",
        options={'HIDDEN'}
        )

    new_release_url: StringProperty(
        name="URL",
        description="Download URL for new release",
        default="",
        options={'HIDDEN'}
        )

    new_release_version: StringProperty(
        name="Version",
        description="Version number of new release (optional)",
        default="",
        options={'HIDDEN'}
        )

    new_release_warning: StringProperty(
        name="Warning",
        description="Warning information for new release (optional)",
        default="",
        options={'HIDDEN'}
        )

    update_error: StringProperty(
        name="Error",
        description="Update error message",
        default="",
        options={'HIDDEN'}
        )

    update_progress: FloatProperty(
        name="Progress",
        description="Update progress indicator",
        min=0.0,
        default=0.0,
        options={'HIDDEN'}
        )

    update_status: EnumProperty(
        name="Status",
        description="Update status",
        items=[
            ('NONE', "", ""),
            ('ERROR', "Update Error", ""),
            ('CHECKING', "Checking for update", ""),
            ('NO_UPDATE', "No update available", ""),
            ('AVAILABLE', "Update available", ""),
            ('DOWNLOADING', "Downloading update", ""),
            ('READY', "Ready to install", ""),
            ],
        default='NONE',
        options={'HIDDEN'}
        )

    version: StringProperty(
        name="Version",
        description="Current installed version string (read-only)",
        get=get_version,
        options=set()
        )

    def _progress_icon(self) -> str:
        progress = self.update_progress % 1
        if   progress < 0.25: return 'PROP_OFF'
        elif progress < 0.5 : return 'PROP_CON'
        elif progress < 0.75: return 'PROP_ON'
        else                : return 'PROP_CON'

    def _release_date(self) -> str:
        try:
            date = self.new_release_date
            date = datetime.date(int(date[:4]), int(date[4:6]), int(date[6:]))
            return date.strftime("%b %d %Y")
        except:
            return ""

    def draw(self, _: 'Context') -> None:

        split = self.layout.split(factor=0.15)
        labels = split.column()
        values = split.column()

        labels.label(text="License Key:")
        values.prop(self, "api_token", text="")

        if self.api_token:
            labels.separator(factor=0.5)
            values.separator(factor=0.5)

            status = self.update_status

            if status == 'CHECKING':
                icon = self._progress_icon()
            else:
                icon = 'URL'

            values.operator(AddonUpdateCheck.bl_idname,
                            icon=icon,
                            text="Check for update",
                            depress=(status == 'CHECKING'))

            labels.separator(factor=0.5)
            values.separator(factor=0.5)

            row = values.row()
            row.alignment = 'RIGHT'
            row.label(text="Include unstable versions:")
            row.prop(self, "include_unstable", text="")

            row = values.row()
            row.alignment = 'RIGHT'
            row.label(text="Check at startup:")
            row.prop(self, "check_for_updates_on_startup", text="")

            labels.separator(factor=0.5)
            values.separator(factor=0.5)
            
            if status == 'ERROR':
                column = values.column(align=True)
                column.box().row().label(icon='ERROR', text="Update Failed")
                column.box().label(text=self.update_error)
                column.box().operator(AddonUpdateReset.bl_idname, text="OK")

            elif status == 'NO_UPDATE':
                column = values.column(align=True)
                column.box().row().label(icon='PLUGIN', text="No Update Available")
                column.box().label(text="You currently have the latest compatible version installed")

            elif status in {'AVAILABLE', 'DOWNLOADING', 'READY', 'INSTALLING'}:
                column = values.column(align=True)

                row = column.box().row()
                row.label(icon='PLUGIN',
                          text="An update is available")

                subrow = row.row()
                subrow.alignment = 'RIGHT'
                subrow.operator(AddonUpdateReset.bl_idname,
                                text="",
                                icon='X',
                                emboss=False)

                split = column.box().row().split(factor=0.3)
                names = split.column()
                value = split.column()

                for key, val in {
                    "Version: "      : self.new_release_version,
                    "Release Date: " : self._release_date(),
                    "Release Notes: ": self.new_release_notes,
                    }.items():
                    if val:
                        names.label(icon='BLANK1', text=key)
                        value.label(text=val)

                box = column.box()

                text = self.new_release_warning
                if text:
                    row = box.row()
                    row.label(icon='ERROR', text=text)

                row = box.row()

                if status == 'AVAILABLE':
                    row.operator(AddonUpdateDownload.bl_idname,
                                 icon='IMPORT',
                                 text="Download")

                elif status == 'DOWNLOADING':
                    row.enabled = False
                    row.operator(AddonUpdateDownload.bl_idname,
                                 icon=self._progress_icon(),
                                 text="Dowload",
                                 depress=True)

                else:# status == 'READY':
                    row.operator(AddonUpdateInstall.bl_idname,
                                 icon='FILE_REFRESH',
                                 text="Update")


def activate(name: str, url: Optional[str]="") -> None:
    global _update_module
    _update_module = name
    global _update_server
    _update_server = url

def deactivate() -> None:
    global _update_module
    _update_module = ""
    global _update_server
    _update_server = ""