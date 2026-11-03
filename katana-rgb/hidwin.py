"""
Minimal Windows HID layer (ctypes, no external dependencies).
Speaks raw HID feature reports via hid.dll / setupapi.dll,
i.e. exactly what OpenRGB's hidapi calls do at the bottom.
"""

import ctypes
from ctypes import wintypes as wt

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
ERROR_INSUFFICIENT_BUFFER = 122
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
HIDP_STATUS_SUCCESS = 0x00110000  # HIDP_ERROR_CODES(0x0, 0)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_HID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.POINTER(wt.ULONG)),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT),
        ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

hid.HidD_GetPreparsedData.restype = wt.BOOL
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wt.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_SetFeature.restype = wt.BOOL
hid.HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
hid.HidD_GetFeature.restype = wt.BOOL
hid.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]


def enumerate_hid_paths():
    """Yield \\\\?\\hid#... device interface paths for every present HID collection."""
    yield from _enumerate_interfaces(GUID_DEVINTERFACE_HID)


GUID_DEVINTERFACE_USB = GUID(
    0xA5DCBF10, 0x6530, 0x11D2,
    (ctypes.c_ubyte * 8)(0x90, 0x1F, 0x00, 0xC0, 0x4F, 0xB9, 0x51, 0xED),
)


def enumerate_usb_paths():
    """Yield \\\\?\\usb#... device interface paths for every present USB device."""
    yield from _enumerate_interfaces(GUID_DEVINTERFACE_USB)


def _enumerate_interfaces(guid):
    devs = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if devs == INVALID_HANDLE_VALUE:
        raise OSError("SetupDiGetClassDevsW failed")
    try:
        i = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                devs, None, ctypes.byref(guid), i, ctypes.byref(did)
            ):
                break
            i += 1

            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                devs, ctypes.byref(did), None, 0, ctypes.byref(need), None
            )
            buf = ctypes.create_string_buffer(need.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W is 8 on x64
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                devs, ctypes.byref(did), buf, need.value, ctypes.byref(need), None
            ):
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                yield path
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devs)


class HidHandle:
    """One open HID top-level collection."""

    def __init__(self, path):
        self.path = path
        self.handle = kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if self.handle in (None, INVALID_HANDLE_VALUE):
            self.handle = kernel32.CreateFileW(
                path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                None, OPEN_EXISTING, 0, None,
            )
        if self.handle in (None, INVALID_HANDLE_VALUE):
            raise OSError(f"CreateFileW failed for {path}: {ctypes.get_last_error()}")
        self.caps = self._caps()

    def _caps(self):
        pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(ctypes.c_void_p(self.handle), ctypes.byref(pp)):
            raise OSError("HidD_GetPreparsedData failed")
        try:
            caps = HIDP_CAPS()
            status = hid.HidP_GetCaps(pp, ctypes.byref(caps))
            if status != HIDP_STATUS_SUCCESS:
                raise OSError(f"HidP_GetCaps status 0x{status & 0xFFFFFFFF:08X}")
            return caps
        finally:
            hid.HidD_FreePreparsedData(pp)

    def send_feature(self, payload):
        """Send a feature report. payload[0] is the report id. Returns True/False."""
        size = max(self.caps.FeatureReportByteLength, len(payload))
        buf = ctypes.create_string_buffer(bytes(payload) + b"\x00" * (size - len(payload)))
        return bool(hid.HidD_SetFeature(ctypes.c_void_p(self.handle), buf, size))

    def send_feature_exact(self, payload):
        buf = ctypes.create_string_buffer(bytes(payload))
        return bool(hid.HidD_SetFeature(ctypes.c_void_p(self.handle), buf, len(payload)))

    def get_feature(self, report_id, length):
        """Read a feature report; returns the raw buffer (first byte = report id)."""
        size = max(self.caps.FeatureReportByteLength, length)
        buf = ctypes.create_string_buffer(size)
        buf[0] = report_id
        ok = hid.HidD_GetFeature(ctypes.c_void_p(self.handle), buf, size)
        return buf.raw if ok else None

    def close(self):
        if self.handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
