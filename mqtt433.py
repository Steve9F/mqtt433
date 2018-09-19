#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import json
import paho.mqtt.client as mqtt
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import time
import datetime as dt
import sys, traceback
import pickle

def on_connect(client, userdata, flags, rc):
    if rc==0:
      client.connected_flag=True
      logger.info("connected ok")
    else:
      logger.info("CNNACK received with code %d." % (rc))

mqtt.Client.connected_flag=False

def on_publish(client, userdata, mid):
    logger.info("mid: "+str(mid))

class rain_tuple(object):
    def __init__(self, time, rain):
        self.time = time
        self.rain = rain
log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
logFile = __file__.replace(".py",'.log')
log_handler = RotatingFileHandler(logFile, mode='a', maxBytes=5*1024*1024,
                                 backupCount=2, encoding=None, delay=0)
log_handler.setFormatter(log_formatter)
log_handler.setLevel(logging.DEBUG)

logger = logging.getLogger('root')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# FORMAT = '%(asctime)-15s %(message)s'
# logging.basicConfig(filename=__file__.replace(".py",'.log'),filemode="w",format=FORMAT,level=logging.DEBUG)
# logging.info("start")

traceback_template = '''Traceback (most recent call last):
  File "%(filename)s", line %(lineno)s, in %(name)s
%(type)s: %(message)s\n''' # Skipping the "actual line" item


mqttc = mqtt.Client("python_pub")
mqttc.on_connect=on_connect
mqttc.username_pw_set(username="USERNAME", password="PASSWORD")
mqttc.loop_start()
logger.info("connecting to broker")
mqttc.connect("localhost", port=1883)

# Rain calc varialbes
#
# rain_counter_midnight - rain_counter_raw at midnight
# rain_counter_today - total rain since midnight
# rain_counter_raw - rain reading from Acurite weather station; rolls over at 99999
rain_counter_midnight = None
rain_counter_today = 0
rain_counter_raw = 0
rain_sensor_id = '1558' # Westher station id
rain_sequence_no = '1' # select second of three identical messages
date_same_day = dt.date.today()
rain_list = []
pop_list = []
rain_old = rain_young = None

# retrieve the rain counter at midnight on restart
try:
  midnight_file = open("midnightb.txt", "rb")
  saved_midnight = pickle.load(midnight_file)
  midnight_file.close()
  logger.debug("Previous midnight data read from file: {} {} ".format(saved_midnight.time, saved_midnight.rain))
  if saved_midnight.time == date_same_day: #<--- need to convert to just date
    rain_counter_midnight = saved_midnight.rain

except IOError:
  logger.debug("File midnightb.txt did not exist")

while not mqttc.connected_flag:
  logger.info("waiting to connect")
  time.sleep(1)

logger.info("main loop")

proc = subprocess.Popen(['rtl_433', '-F', 'json', '-R', '40', '-R', '74'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

while True:
#    logger.debug("listening")
    input = proc.stdout.readline()
    payload = json.loads(input.decode("utf-8"))
    logger.debug(payload)
    try:
        if (str(payload['sensor_id']) == rain_sensor_id) and (str(payload['sequence_num']) == rain_sequence_no) :
            # need to add code to skip the other sequence numbers
            try:
                rain_counter_raw = payload['raincounter_raw']
                logger.debug("*** rain counter raw: %s", rain_counter_raw)

                if rain_counter_midnight is None:
                    rain_counter_midnight = rain_counter_raw
                    # save rain counter at midnight to a file to persist over restarts
                    midnight_file = open("midnightb.txt", "wb")
                    pickle.dump(rain_tuple(dt.date.today(), rain_counter_raw), midnight_file, -1)
                    midnight_file.close()

                if dt.date.today() == date_same_day:
                    if rain_counter_raw < rain_counter_midnight:
                        rain_counter_midnight = -rain_counter_today

                    rain_counter_today = rain_counter_raw - rain_counter_midnight
                else:
                    # this needs some work to calcuate a more accurate number if the time between readings across midnight is significant
                    rain_counter_midnight = rain_counter_raw
                    rain_counter_today = 0
                    date_same_day = dt.date.today()
                    # save rain counter at midnight to a file to persist over restarts
                    midnight_file = open("midnightb.txt", "wb")
                    pickle.dump(rain_tuple(date_same_day, rain_counter_midnight), midnight_file, -1)
                    midnight_file.close()

                payload['raincounter_today'] = rain_counter_today
                logger.debug("*** rain counter today: %s", rain_counter_today)

#
# Calculate the rain over the last 60 minutes
#
                rain_time = dt.datetime.strptime(payload['time'], "%Y-%m-%d %H:%M:%S")
                rain_60ago = rain_time - dt.timedelta(seconds=3600) # 120 for testing - should be 3600
# delete                rain_obj = rain_tuple(rain_time, rain_counter_raw)
# delete                print("rain object: {}".format(rain_obj))
                rain_list.append(rain_tuple(rain_time, rain_counter_raw))

#                print "rain 60ago: ", (rain_60ago,)

                for rain_index, rain_reading in enumerate(rain_list):
                    # print("rain reading {} rain 60 {} < {}".format(rain_reading.time, rain_60ago, rain_reading.time < rain_60ago))
                    if rain_reading.time < rain_60ago:
                        rain_old = rain_reading
                        # print("rain_old time {} rain {}".format(rain_old.time, rain_old.rain))
                        pop_list.append(rain_index)
                    else:
                        rain_young = rain_reading
                        # print("rain_young time {} rain {}".format(rain_young.time, rain_young.rain))
                        break
                for pop_index in pop_list:
#                    print(rain_list[0])
                    rain_list.pop(0)
                pop_list = []

                if rain_old:
                    newreading = rain_young.rain
                    oldreading = rain_old.rain
                    # print("Rain young {} old {}".format(newreading, oldreading))
                    newtime = time.mktime(rain_young.time.timetuple())
                    oldtime = time.mktime(rain_old.time.timetuple())
                    deltatime = newtime - oldtime
                    time60 = time.mktime(rain_60ago.timetuple())
                    skewtime = newtime - time60
                    rain_counter_60m = rain_counter_raw - (newreading - ((newreading - oldreading) * skewtime / deltatime))
                elif rain_young.time == rain_60ago:
                    rain_counter_60m = rain_counter_raw - rain_young.rain
                else:
                    rain_counter_60m = None

                payload['raincounter_60m'] = rain_counter_60m
                logger.info("raincounter_60m: %s", rain_counter_60m)
                logger.debug("*** rain counter list: %s", rain_list)

            except KeyError:
                pass

            # except Exception as x:
            #     logger.warning("exception %s" % x)
            #     logger.warning("input:'%s'" % input)
            #
            except KeyboardInterrupt:
                sys.stdout.flush()
                mqttc.loop_stop()
                mqttc.disconnect()
                logger.warning("exit")
                break

            except:
#                exc_type, exc_value, exc_traceback = sys.exc_info()
                traceback_details = {
                                     'filename': sys.exc_info()[2].tb_frame.f_code.co_filename,
                                     'lineno'  : sys.exc_info()[2].tb_lineno,
                                     'name'    : sys.exc_info()[2].tb_frame.f_code.co_name,
                                     'type'    : sys.exc_info()[0].__name__,
                                     'message' : sys.exc_info()[1].message, # or see traceback._some_str()
                                    }

#                del(exc_type, exc_value, exc_traceback) # So we don't leave our local labels/objects dangling
                # This still isn't "completely safe", though!
                # "Best (recommended) practice: replace all exc_type, exc_value, exc_traceback
                # with sys.exc_info()[0], sys.exc_info()[1], sys.exc_info()[2]

                # print
                # print traceback.format_exc()
                # print
                # print traceback_template % traceback_details
                # print
                logger.error(traceback.format_exc())
                logger.error(traceback_template % traceback_details)


        (rc, mid) = mqttc.publish("hass/sensor/acurite/id_" + str(payload['sensor_id']) + "-" + str(payload['message_type']) + "-" + str(payload['sequence_num']) , json.dumps(payload) , retain=True)
        logger.debug(payload)
        logger.debug("rc=%s, mid=%s" % (rc, mid))
#        print("{}: {}".format(dt.datetime.now(), payload))
#        logger.debug("published")
    except KeyError:
        try:
            (rc, mid) = mqttc.publish("hass/sensor/acurite/id_" + str(payload['id']) , json.dumps(payload) , retain=True)
            logger.debug("rc=%s, mid=%s" % (rc, mid))
#            print("{}: {}".format(dt.datetime.now(), payload))
#            logger.debug("published")
        except Exception as x:
            logger.warning("exception %s" % x)
            logger.warning("input:'%s'" % input)
    except Exception as x:
        logger.warning("exception %s" % x)
        logger.warning("input:'%s'" % input)
    except KeyboardInterrupt:
        sys.stdout.flush()
        mqttc.loop_stop()
        mqttc.disconnect()
        logger.warning("exit")
        break
