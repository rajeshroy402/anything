#!/usr/bin/env python3

import sys

sys.path.append('../')
import gi
import configparser

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
from gi.repository import GLib
from ctypes import *
import time
import sys
import math
import platform
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call
from common.FPS import GETFPS
from datetime import time
import datetime as dt
import os
import pyds
from datetime import datetime

fps_streams = {}

MAX_DISPLAY_LEN = 64

global PGIE_CLASS_ID_SACKS
PGIE_CLASS_ID_SACKS = 0

MUXER_OUTPUT_WIDTH = 1920
MUXER_OUTPUT_HEIGHT = 1080
MUXER_BATCH_TIMEOUT_USEC = 4000000
TILED_OUTPUT_WIDTH = 1280
TILED_OUTPUT_HEIGHT = 720
GST_CAPS_FEATURES_NVMM = "memory:NVMM"
OSD_PROCESS_MODE = 0
OSD_DISPLAY_TEXT = 1
pgie_classes_str = ["sacks"]
CAMERA_ID = '1_1'
N_FRAMES_ZERO = 5000

from utils import query_push_counting, query_push_log, query_all_data, get_mydb_cursor, commit_and_close


# from config import


# nvanlytics_src_pad_buffer_probe  will extract metadata received on nvtiler sink pad
# and update params for drawing rectangle, object information etc.
def nvanalytics_src_pad_buffer_probe(pad, info, u_data):
    global to_count
    global count_zero
    global cum_entry_at_last_push
    global cum_exit_at_last_push

    now = dt.datetime.now()
    if now.hour == 9 and now.minute == 0:
        print("close")
        sys.exit(main(sys.argv))

    frame_number = 0
    num_rects = 0
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return

    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting is done by pyds.NvDsFrameMeta.cast()
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        frame_number = frame_meta.frame_num
        l_obj = frame_meta.obj_meta_list
        num_rects = frame_meta.num_obj_meta
        obj_counter = {
            PGIE_CLASS_ID_SACKS: 0
        }
        print("#" * 50)
        while l_obj:
            try:
                # Note that l_obj.data needs a cast to pyds.NvDsObjectMeta
                # The casting is done by pyds.NvDsObjectMeta.cast()
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break
            obj_counter[obj_meta.class_id] += 1
            l_user_meta = obj_meta.obj_user_meta_list
            # Extract object level meta data from NvDsAnalyticsObjInfo
            while l_user_meta:
                try:
                    user_meta = pyds.NvDsUserMeta.cast(l_user_meta.data)
                    if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSOBJ.USER_META"):
                        user_meta_data = pyds.NvDsAnalyticsObjInfo.cast(user_meta.user_meta_data)
                        if user_meta_data.dirStatus: print(
                            "Object {0} moving in direction: {1}".format(obj_meta.object_id, user_meta_data.dirStatus))
                        if user_meta_data.lcStatus: print(
                            "Object {0} line crossing status: {1}".format(obj_meta.object_id, user_meta_data.lcStatus))
                        if user_meta_data.ocStatus: print(
                            "Object {0} overcrowding status: {1}".format(obj_meta.object_id, user_meta_data.ocStatus))
                        if user_meta_data.roiStatus: print(
                            "Object {0} roi status: {1}".format(obj_meta.object_id, user_meta_data.roiStatus))
                except StopIteration:
                    break

                try:
                    l_user_meta = l_user_meta.next
                except StopIteration:
                    break
            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        # Get meta data from NvDsAnalyticsFrameMeta
        l_user = frame_meta.frame_user_meta_list
        while l_user:
            try:
                user_meta = pyds.NvDsUserMeta.cast(l_user.data)
                if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSFRAME.USER_META"):
                    user_meta_data = pyds.NvDsAnalyticsFrameMeta.cast(user_meta.user_meta_data)
                    if user_meta_data.objInROIcnt: print("Objs in ROI: {0}".format(user_meta_data.objInROIcnt))
                    if user_meta_data.objLCCumCnt:
                        cumulative_entry = user_meta_data.objLCCumCnt["Entry"]
                        print("Linecrossing Cumulative Entry: {0}".format(cumulative_entry))
                    if user_meta_data.objLCCumCnt:
                        cumulative_exit = user_meta_data.objLCCumCnt["Exit"]
                        print("Linecrossing Exit: {0}".format(cumulative_exit))

                    if user_meta_data.objLCCurrCnt:
                        current_entry = user_meta_data.objLCCurrCnt["Entry"]
                        print("Linecrossing Current Frame - Entry: {0}".format(current_entry))
                    if user_meta_data.objLCCurrCnt:
                        current_exit = user_meta_data.objLCCurrCnt["Exit"]
                        print("Linecrossing Current Frame - Exit: {0}".format(current_exit))

                    print('to_count: ', to_count)
                    
                    #print("Aggregate: ", abs(aggregate))
                    if (current_exit != 0) or (current_entry != 0):
                        to_count = True
                        count_zero = 0

                    if to_count:
                        count_zero += 1

                    cum_entry_since_last_push = cumulative_entry - cum_entry_at_last_push
                    cum_exit_since_last_push = cumulative_exit - cum_exit_at_last_push

                    print('count_zero: ', count_zero)
                    if count_zero == N_FRAMES_ZERO:
                        print('cum_entry_since_last_push: ', cum_entry_since_last_push)
                        print('cum_exit_since_last_push: ', cum_exit_since_last_push)

                        # push counting stats
                        aggregate = cum_exit_since_last_push - cum_entry_since_last_push
                        date = datetime.now().date()
                        params = (date, CAMERA_ID, cum_entry_since_last_push, cum_exit_since_last_push, abs(aggregate))
                        _ = query_all_data(cursor, query_push_counting, params)
                        last_id = query_all_data(cursor, query_last_counting_id)[0][0]
                        mydb.commit()

                        # push log data
                        params = (date, datetime.now().time(), 'counting', 'push counting stats', CAMERA_ID, last_id)
                        _ = query_all_data(cursor, query_push_log, params)
                        mydb.commit()

                        count_zero = 0
                        to_count = False

                        cum_entry_at_last_push = cumulative_entry
                        cum_exit_at_last_push = cumulative_exit

                    # if user_meta_data.ocStatus: print("Overcrowding status: {0}".format(user_meta_data.ocStatus))


            except StopIteration:
                break
            try:
                l_user = l_user.next
            except StopIteration:
                break

        print("Frame Number=", frame_number, "stream id=", frame_meta.pad_index, "Number of Objects=", num_rects,
              "Sack_count=", obj_counter[PGIE_CLASS_ID_SACKS])
        # Get frame rate through this probe
        fps_streams["stream{0}".format(frame_meta.pad_index)].get_fps()
        try:
            l_frame = l_frame.next
        except StopIteration:
            break
        print("#" * 50)

    return Gst.PadProbeReturn.OK


def cb_newpad(decodebin, decoder_src_pad, data):
    print("In cb_newpad\n")
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    source_bin = data
    features = caps.get_features(0)

    # Need to check if the pad created by the decodebin is for video and not
    # audio.
    print("gstname=", gstname)
    if (gstname.find("video") != -1):
        # Link the decodebin pad only if decodebin has picked nvidia
        # decoder plugin nvdec_*. We do this by checking if the pad caps contain
        # NVMM memory features.
        print("features=", features)
        if features.contains("memory:NVMM"):
            # Get the source bin ghost pad
            bin_ghost_pad = source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write("Failed to link decoder src pad to source bin ghost pad\n")
        else:
            sys.stderr.write(" Error: Decodebin did not pick nvidia decoder plugin.\n")


def decodebin_child_added(child_proxy, Object, name, user_data):
    print("Decodebin child added:", name, "\n")
    if (name.find("decodebin") != -1):
        Object.connect("child-added", decodebin_child_added, user_data)


def create_source_bin(index, uri):
    print("Creating source bin")

    # Create a source GstBin to abstract this bin's content from the rest of the
    # pipeline
    bin_name = "source-bin-%02d" % index
    print(bin_name)
    nbin = Gst.Bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")

    # Source element for reading from the uri.
    # We will use decodebin and let it figure out the container format of the
    # stream and the codec and plug the appropriate demux and decode plugins.
    uri_decode_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not uri_decode_bin:
        sys.stderr.write(" Unable to create uri decode bin \n")
    # We set the input uri to the source element
    uri_decode_bin.set_property("uri", uri)
    # Connect to the "pad-added" signal of the decodebin which generates a
    # callback once a new pad for raw data has beed created by the decodebin
    uri_decode_bin.connect("pad-added", cb_newpad, nbin)
    uri_decode_bin.connect("child-added", decodebin_child_added, nbin)

    # We need to create a ghost pad for the source bin which will act as a proxy
    # for the video decoder src pad. The ghost pad will not have a target right
    # now. Once the decode bin creates the video decoder and generates the
    # cb_newpad callback, we will set the ghost pad target to the video decoder
    # src pad.
    Gst.Bin.add(nbin, uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.write(" Failed to add ghost pad in source bin \n")
        return None
    return nbin


def main(args):
    global mydb
    global cursor
    global query_last_counting_id
    global to_count
    global count_zero
    global cum_entry_at_last_push
    global cum_exit_at_last_push

    mydb, cursor = get_mydb_cursor()
    query_last_counting_id = 'SELECT id FROM stats_counting ORDER BY id DESC LIMIT 1'
    to_count = False
    count_zero = 0
    cum_entry_at_last_push = 0
    cum_exit_at_last_push = 0

    # Check input arguments
    if len(args) < 2:
        sys.stderr.write("usage: %s <uri1> [uri2] ... [uriN]\n" % args[0])
        sys.exit(1)

    for i in range(0, len(args) - 1):
        fps_streams["stream{0}".format(i)] = GETFPS(i)
    number_sources = len(args) - 1

    # Standard GStreamer initialization
    GObject.threads_init()
    Gst.init(None)

    # Create gstreamer elements */
    # Create Pipeline element that will form a connection of other elements
    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()
    is_live = False

    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")
    print("Creating streamux \n ")

    # Create nvstreammux instance to form batches from one or more sources.
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")

    pipeline.add(streammux)
    for i in range(number_sources):
        print("Creating source_bin ", i, " \n ")
        uri_name = args[i + 1]
        if uri_name.find("rtsp://") == 0:
            is_live = True
        source_bin = create_source_bin(i, uri_name)
        if not source_bin:
            sys.stderr.write("Unable to create source bin \n")
        pipeline.add(source_bin)
        padname = "sink_%u" % i
        sinkpad = streammux.get_request_pad(padname)
        if not sinkpad:
            sys.stderr.write("Unable to create sink pad bin \n")
        srcpad = source_bin.get_static_pad("src")
        if not srcpad:
            sys.stderr.write("Unable to create src pad bin \n")
        srcpad.link(sinkpad)
    queue1 = Gst.ElementFactory.make("queue", "queue1")
    queue2 = Gst.ElementFactory.make("queue", "queue2")
    queue3 = Gst.ElementFactory.make("queue", "queue3")
    queue4 = Gst.ElementFactory.make("queue", "queue4")
    queue5 = Gst.ElementFactory.make("queue", "queue5")
    queue6 = Gst.ElementFactory.make("queue", "queue6")
    queue7 = Gst.ElementFactory.make("queue", "queue7")
    pipeline.add(queue1)
    pipeline.add(queue2)
    pipeline.add(queue3)
    pipeline.add(queue4)
    pipeline.add(queue5)
    pipeline.add(queue6)
    pipeline.add(queue7)

    print("Creating Pgie \n ")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")

    print("Creating nvtracker \n ")
    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    if not tracker:
        sys.stderr.write(" Unable to create tracker \n")

    print("Creating nvdsanalytics \n ")
    nvanalytics = Gst.ElementFactory.make("nvdsanalytics", "analytics")
    if not nvanalytics:
        sys.stderr.write(" Unable to create nvanalytics \n")
    nvanalytics.set_property("config-file", "config_nvdsanalytics.txt")

    #print("Creating tiler \n ")
    #tiler = Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    #if not tiler:
    #    sys.stderr.write(" Unable to create tiler \n")

    #print("Creating nvvidconv \n ")
    #nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    #if not nvvidconv:
    #    sys.stderr.write(" Unable to create nvvidconv \n")

    #print("Creating nvosd \n ") ### Comment
    #nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay") ### Comment
    #if not nvosd: ### Comment
    #    sys.stderr.write(" Unable to create nvosd \n") ### Comment
    #nvosd.set_property('process-mode', OSD_PROCESS_MODE)
    #nvosd.set_property('display-text', OSD_DISPLAY_TEXT)

    #if (is_aarch64()):
    #    print("Creating transform \n ")
    #    transform = Gst.ElementFactory.make("nvegltransform", "nvegl-transform")
    #    if not transform:
    #        sys.stderr.write(" Unable to create transform \n")

    print("Creating EGLSink \n")
    sink = Gst.ElementFactory.make("fakesink", "nvvideo-renderer")
    if not sink:
        sys.stderr.write(" Unable to create egl sink \n")

    if is_live:
        print("Atleast one of the sources is live")
        streammux.set_property('live-source', 1)

    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', number_sources)
    streammux.set_property('batched-push-timeout', 4000000)
    pgie.set_property('config-file-path', "config_infer_primary.txt")
    pgie_batch_size = pgie.get_property("batch-size")
    if (pgie_batch_size != number_sources):
        print("WARNING: Overriding infer-config batch-size", pgie_batch_size, " with number of sources ",
              number_sources, " \n")
        pgie.set_property("batch-size", number_sources)
    #tiler_rows = int(math.sqrt(number_sources))
    #tiler_columns = int(math.ceil((1.0 * number_sources) / tiler_rows))
    #tiler.set_property("rows", tiler_rows)
    #tiler.set_property("columns", tiler_columns)
    #tiler.set_property("width", TILED_OUTPUT_WIDTH)
    #tiler.set_property("height", TILED_OUTPUT_HEIGHT)
    sink.set_property("sync", 0)

    # Set properties of tracker
    config = configparser.ConfigParser()
    config.read('dsnvanalytics_tracker_config.txt')
    config.sections()

    for key in config['tracker']:
        if key == 'tracker-width':
            tracker_width = config.getint('tracker', key)
            tracker.set_property('tracker-width', tracker_width)
        if key == 'tracker-height':
            tracker_height = config.getint('tracker', key)
            tracker.set_property('tracker-height', tracker_height)
        if key == 'gpu-id':
            tracker_gpu_id = config.getint('tracker', key)
            tracker.set_property('gpu_id', tracker_gpu_id)
        if key == 'll-lib-file':
            tracker_ll_lib_file = config.get('tracker', key)
            tracker.set_property('ll-lib-file', tracker_ll_lib_file)
        if key == 'll-config-file':
            tracker_ll_config_file = config.get('tracker', key)
            tracker.set_property('ll-config-file', tracker_ll_config_file)
        if key == 'enable-batch-process':
            tracker_enable_batch_process = config.getint('tracker', key)
            tracker.set_property('enable_batch_process', tracker_enable_batch_process)
        if key == 'enable-past-frame':
            tracker_enable_past_frame = config.getint('tracker', key)
            tracker.set_property('enable_past_frame', tracker_enable_past_frame)

    print("Adding elements to Pipeline \n")
    pipeline.add(pgie)
    pipeline.add(tracker)
    pipeline.add(nvanalytics)
    #pipeline.add(tiler)
    #pipeline.add(nvvidconv)
    #pipeline.add(nvosd) # Comment

    #if is_aarch64():
    #    pipeline.add(transform)
    pipeline.add(sink)

    # We link elements in the following order:
    # sourcebin -> streammux -> nvinfer -> nvtracker -> nvdsanalytics ->
    # nvtiler -> nvvideoconvert -> nvdsosd -> sink
    print("Linking elements in the Pipeline \n")
    streammux.link(queue1)
    queue1.link(pgie)
    pgie.link(queue2)
    queue2.link(tracker)
    tracker.link(queue3)
    queue3.link(nvanalytics)
    nvanalytics.link(queue4)
    #queue4.link(tiler)
    #tiler.link(queue5)
    #queue5.link(nvvidconv)
    #nvvidconv.link(queue6)
    #queue6.link(nvosd) ### Comment
    #if is_aarch64():
        #nvosd.link(queue7)
        #queue7.link(transform)
        #transform.link(sink)
    #else:
        #nvosd.link(queue7)
        #queue7.link(sink)

    queue4.link(sink)

    # create an event loop and feed gstreamer bus mesages to it
    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)
    nvanalytics_src_pad = nvanalytics.get_static_pad("src")
    if not nvanalytics_src_pad:
        sys.stderr.write(" Unable to get src pad \n")
    else:
        nvanalytics_src_pad.add_probe(Gst.PadProbeType.BUFFER, nvanalytics_src_pad_buffer_probe, 0)

    # List the sources
    print("Now playing...")
    for i, source in enumerate(args):
        if (i != 0):
            print(i, ": ", source)

    print("Starting pipeline \n")
    # start play back and listed to events		
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    # cleanup
    print("Exiting app\n")
    pipeline.set_state(Gst.State.NULL)
    commit_and_close(mydb, cursor)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
