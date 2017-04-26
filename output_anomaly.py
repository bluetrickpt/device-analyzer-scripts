#!/usr/bin/env python
#
# Copyright 2016 Kelly Widdicks, Alastair R. Beresford
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gzip
import sys
import os
import csv
import io
import glob
import numpy as np
import subprocess
from collections import namedtuple, OrderedDict
import dateutil.parser
from datetime import datetime, timedelta
from functools import reduce

global no_of_ignored_files

fields_da = ('Entry','Num','Date','EntryType','Value')
DARecord = namedtuple('DARecord', fields_da)
def read_file(path):
    try:
        with io.TextIOWrapper(io.BufferedReader(gzip.open(path))) as data:
            for line in data:
                #Repack variable number of items per line into five expected items
                #(Problem is internal DA format uses ';' to separate csv items as well
                # as to separate app names inside the 'Value' field.)
                e = line.split(';')
                value = reduce(lambda x, y: x + ',' + y, e[4:])
                repacked = e[0:4] + [value]
                yield DARecord._make(repacked)
    except:
        print('Failed to read file: ' + path)

fields_filename = ('i', 'FileName', 'Start', 'End', 'Days', 'PropData', 'InUK', 'OutUK', 'PropUK')
FileNameRecord = namedtuple('FileNameRecord', fields_filename)
def read_file_names(path):
    with open(path, 'rU') as data:
        csv.field_size_limit(sys.maxsize)
        reader = csv.reader(data, delimiter=' ')
        for row in map(FileNameRecord._make, reader):
            yield row

fields_lancs = ('Entry','Num','Date','EntryType','Value')
DARecordLancs = namedtuple('DARecordLancs', fields_lancs)
def read_file_lancs(path):
    try:
        with open(path, 'rU') as data:
            csv.field_size_limit(sys.maxsize)
            reader = csv.reader(data, delimiter=';')
            for row in map(DARecord._make, reader):
                yield row
    except:
        print('Failed to read file: ' + path)

FileNameRecordLancs = namedtuple('FileNameRecordLancs', ('FileName'))
def read_file_names_lancs(path):
    with open(path, 'rU') as data:
        csv.field_size_limit(sys.maxsize)
        reader = csv.reader(data, delimiter='\n')
        for row in map(FileNameRecordLancs._make, reader):
            yield row

def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        print('Output path exists')

def get_t_gap(first, second):
    return (dateutil.parser.parse(second) - dateutil.parser.parse(first)).total_seconds()

def search_dates(file_path, lancs):
    start_date = None
    end_date = None

    for row in (read_file_lancs(file_path) if lancs else read_file(file_path)):
        date_time = row.Date
        if '(invalid date)' in date_time:
            continue

        if start_date == None:
            start_date = date_time

        end_date = date_time

    if start_date == None or end_date == None:
        return None, None

    return start_date[:-9], end_date[:-9]

def get_start_end_dates(file_path, lancs):
    start = None
    end = None
    if lancs:
        # start = str(subprocess.check_output(['head', '-1', file_path])).split(';')[2][:-9]
        # end = str(subprocess.check_output(['tail', '-1', file_path])).split(';')[2][:-9]
        # if '(invalid date)' in start or '(invalid date)' in end:
        start, end = search_dates(file_path, lancs)
    else:
        start, end = search_dates(file_path, lancs)

    # If cannot find valid dates, return None to ignore this device in the analysis
    if start == None or end == None:
        return None, None

    start_date_time = datetime.strptime(start, '%Y-%m-%dT%H:%M:%S')
    end_date_time = datetime.strptime(end, '%Y-%m-%dT%H:%M:%S')

    start_date_to_return = None
    end_date_to_return = None
    # If before 4am, then the date is fine - else add a day
    if start_date_time.time().hour < 4:
        start_date_to_return = start_date_time
    else:
        start_date_to_return = (start_date_time + timedelta(days=1))
    # If after or equal to 4am, then the date is fine - else remove a day
    if end_date_time.time().hour >= 4:
        end_date_to_return = end_date_time
    else:
        end_date_to_return = (end_date_time - timedelta(days=1))

    # Check the start and end dates are at least 12 days apart - if not, return None to ignore this device in the analysis
    difference = end_date_to_return.date() - start_date_to_return.date()
    if difference.days < 14:
        return None, None

    return (start_date_to_return.strftime('%Y-%m-%d'))+'T04:00:00', (end_date_to_return.strftime('%Y-%m-%d'))+'T04:00:00'

def parse_file(file_path, lancs, fname, start_date, end_date):
    global no_of_ignored_files

    logs_to_parse = ['app', 'screen', 'hf', 'net']

    current_hour = None
    current_day = None
    current_weekday = None
    no_of_days = 0

    app_foreground_use = {}

    screen_on = False
    screen_unlocked = False
    last_importance_app_pid = None

    last_app_data = {}
    all_data_rx = [[] for hour in range(0,24)]
    all_data_tx = [[] for hour in range(0,24)]

    no_of_days_week = [0 for day in range(0,7)]

    ids_names = {}
    current_app_name_id_mapping = {}
    app_data = {}

    for row in (read_file_lancs(file_path) if lancs else read_file(file_path)):
        row_entry_type = row.EntryType
        entry_val = row_entry_type.split('|')
        row_date = row.Date
        date_time = row_date.rsplit('T')
        row_value = row.Value.strip()

        if entry_val[0] not in logs_to_parse or row_date == '(invalid date)':
            continue

        if row_date[:-9] < start_date or row_date[:-9] >= end_date:
            continue

        if current_day != date_time[0]:
            current_day = date_time[0]
            current_weekday = datetime.strptime(date_time[0], '%Y-%m-%d').weekday()
            no_of_days_week[current_weekday]+=1
            no_of_days+=1

        current_hour = int(date_time[1].split(':')[0])

        # An app is in the foreground so log its process id
        if 'importance' in entry_val and 'foreground' in row_value:
            last_importance_app_pid = entry_val[1]
        # Get the name of the app currently in the foreground
        elif 'app' in entry_val and 'name' in entry_val and last_importance_app_pid != None:

            #row_value contains "<app name>:<play store group>" so retrieve just app name:
            app_name = row_value.split(":")[0]

            # The app pids for the app importance and app name logs don't match, so ignore it
            if entry_val[1] == last_importance_app_pid:
                # The user is using the device
                if screen_on and screen_unlocked:
                    # Increment the number of times it was in the foreground
                    if app_name not in app_foreground_use:
                        app_foreground_use[app_name] = [[0 for x in range(0,24)] for y in range(0,7)]
                    app_foreground_use[app_name][current_weekday][current_hour]+=1

            last_importance_app_pid = None
        # Screen locked/unlocked
        elif row_entry_type.startswith('hf|locked'):
            if 'true' in row_value:
                screen_unlocked = False
            else:
                screen_unlocked = True
        # Screen on/off
        elif row_entry_type.startswith('screen|power'):
            if 'off' in row_value:
                screen_on = False
            else:
                screen_on = True
        # App data
        elif row_entry_type.startswith('net|app'):
            app_id = entry_val[2]
            app_name = None
            for key, val in current_app_name_id_mapping.items():
                if val == app_id:
                    app_name = key
            if app_name == None:
                continue

            if entry_val[3] == 'rx_bytes':
                app_last_rx = app_data[app_name][0]
                if app_last_rx == None:
                    pass
                elif int(row_value) > app_last_rx:
                    app_data[app_name][1][current_weekday][current_hour].append(int(row_value) - app_last_rx)
                elif int(row_value) < app_last_rx:
                    app_data[app_name][1][current_weekday][current_hour].append(int(row_value))
                app_data[app_name][0] = int(row_value)
            elif entry_val[3] == 'tx_bytes':
                app_last_tx = app_data[app_name][2]
                if app_last_tx == None:
                    pass
                elif int(row_value) > app_last_tx:
                    app_data[app_name][3][current_weekday][current_hour].append(int(row_value) - app_last_tx)
                elif int(row_value) < app_last_tx:
                    app_data[app_name][3][current_weekday][current_hour].append(int(row_value))
                app_data[app_name][2] = int(row_value)
        # App installed logs
        elif row_entry_type.startswith('app|installed'):
            for app_entry in row_value.split(','):
                installed_details = app_entry.split('@')
                if len(installed_details) > 1:
                    temp_name = installed_details[0]
                    app_info = installed_details[1].split(':')
                    temp_app_id = app_info[len(app_info) - 2]
                    if temp_name not in current_app_name_id_mapping:
                        app_data[temp_name] = [None, [[[] for x in range(0,24)] for y in range(0,7)], None, [[[] for x in range(0,24)] for y in range(0,7)]]

                    # Remove old mapping if it exists
                    if temp_app_id not in ids_names:
                        ids_names[temp_app_id] = temp_name
                    elif ids_names[temp_app_id] != temp_name:
                        for key, val in current_app_name_id_mapping.items():
                            if val == temp_app_id and key != temp_name:
                                current_app_name_id_mapping[key] = ''
                        ids_names[temp_app_id] = temp_name

                    current_app_name_id_mapping[temp_name] = temp_app_id

    if no_of_days >= 14:
        saturday_total_rx = [0 for i in range(0,24)]
        saturday_total_tx = [0 for i in range(0,24)]
        saturday_total = [0 for i in range(0,24)]

        index_of_saturday = 5
        no_of_saturdays = no_of_days_week[index_of_saturday]
        
        for app, data in app_data.items():
            mean_rx = [0 for i in range(0,24)]
            mean_tx = [0 for i in range(0,24)]

            if no_of_saturdays != 0:
                mean_rx = [(sum(ihour)/no_of_saturdays) for ihour in data[1][index_of_saturday]]
                mean_tx = [(sum(ihour)/no_of_saturdays) for ihour in data[3][index_of_saturday]]

                if not all(hour == 0 for hour in mean_rx):
                    for hour in range(0,24):
                        saturday_total_rx[hour] = saturday_total_rx[hour] + mean_rx[hour]
                if not all(hour == 0 for hour in mean_tx):
                    for hour in range(0,24):
                        saturday_total_tx[hour] = saturday_total_tx[hour] + mean_tx[hour]
        with open('anomaly_output/saturday_totals.csv', 'a') as f:
            f.write(fname)
            for hour in range(0,24):
                saturday_total[hour] = saturday_total_rx[hour] + saturday_total_tx[hour]
                f.write(',{0}'.format(str(saturday_total[hour])))
            f.write('\n')

    else:
        no_of_ignored_files+=1
        print('Not adding {0} to summary, as no. of actual data days: {1}'.format(file_path, no_of_days))


if __name__ == '__main__':
    global no_of_ignored_files

    pathOfIdsFile = sys.argv[1]
    pathOfFiles = sys.argv[2]
    lancs = bool(len(sys.argv) > 3)

    no_of_ignored_files = 0

    startTime = datetime.now()

    # Make sure 'out/' folder exists and reset/create output files
    make_sure_path_exists('anomaly_output/')

    with open('anomaly_output/saturday_totals.csv', 'w') as f:
        f.write('hour')
        for hour in range(0,24):
            f.write(',{0}'.format(str(hour)))
        f.write('\n')

    for file in (read_file_names_lancs(pathOfIdsFile) if lancs else read_file_names(pathOfIdsFile)):
        fname = file.FileName
        fullfpath = pathOfFiles + file.FileName + '.csv'
        if not lancs:
            fullfpath = fullfpath + '.gz'
        print("Parsing file: " + fname)
        start_date, end_date = get_start_end_dates(fullfpath, lancs)
        if start_date == None or end_date == None:
            print("No start or end dates, or under 14 days of logging, for file: " + fname)
            no_of_ignored_files+=1
        else:
            parse_file(fullfpath, lancs, fname, start_date, end_date)

    # **** For checking timings *****
    endFilesTime = datetime.now()
    print("All files summarised in {0}".format(str((endFilesTime - startTime))))
    print("No. of ignored files: {0}".format(str(no_of_ignored_files)))
