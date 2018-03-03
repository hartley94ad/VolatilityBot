#! /usr/bin/python
import logging
import os
import re

import pefile
from pefile import PEFormatError

from volatilitybot.lib.common import pslist
from volatilitybot.lib.common.pe_utils import fix_pe_from_memory, static_analysis
from volatilitybot.lib.common.utils import get_workdir_path, calc_sha256, calc_md5, calc_ephash, calc_imphash, calc_sha1
from volatilitybot.lib.core.es_utils import DataBaseConnection
from volatilitybot.lib.core.memory_utils import execute_volatility_command
from volatilitybot.lib.core.sample import SampleDump
from volatilitybot.post_processing.utils.submiter import send_dump_analysis_task


def create_golden_image(self):
    pass


NAME = 'injected_code_dump'
TIMEOUT = 120


def run_extractor(memory_instance, malware_sample, machine_instance=None):
    pslist_new_data = pslist.get_new_pslist(memory_instance)

    target_dump_dir = os.path.join(get_workdir_path(malware_sample), 'injected')
    try:
        os.mkdir(target_dump_dir)
    except FileExistsError:
        pass

    output = execute_volatility_command(memory_instance, 'malfind', extra_flags='-D {}/'.format(target_dump_dir),
                                        has_json_output=False)
    db = DataBaseConnection()

    # Find malfind injections that are binaries, and rename them
    for single_dump in os.scandir(target_dump_dir):
        splitted_line = re.split('\.', single_dump.path.rstrip('\n'))
        logging.info('offset: {}, Imagebase: {}'.format(splitted_line[1], splitted_line[2]))
        offset = splitted_line[1]
        imagebase = splitted_line[2]

        # Verify if it is PE or not
        try:
            pe = pefile.PE(single_dump.path)
            isPE = True
        except PEFormatError:
            isPE = False

        if isPE:
            db.add_tag("Injects_Code", malware_sample)
            logging.info('[*] Processing {}'.format(single_dump.path))
            logging.info('offset: %s, Imagebase: %s'.format(offset, imagebase))
            logging.info('Altering image base: {} => {}'.format(pe.OPTIONAL_HEADER.ImageBase, imagebase))

            fixed_pe = fix_pe_from_memory(pe, imagebase=imagebase)

            # Get original process name
            process_name = "unknown"

            for proc_gi in pslist_new_data:
                if str(hex(proc_gi['Offset(V)'])) == offset:
                    logging.info("Found process name: {}".format(proc_gi['Name']))
                    process_name = proc_gi['Name']
                    pid = str(proc_gi['PID'])
                    break

            outputpath = os.path.join(target_dump_dir, process_name + '.' + offset + '.' + imagebase + '.fixed_bin')
            fixed_pe.write(filename=outputpath)
            pe.close()

            if process_name != 'unknown':
                current_dump = SampleDump(outputpath)

                current_dump.dump_data.update({
                    'md5': calc_md5(outputpath),
                    'sha1': calc_sha1(outputpath),
                    'sha256': calc_sha256(outputpath),
                    'imphash': calc_imphash(outputpath),
                    'ephash': calc_ephash(outputpath),
                    'process_name': process_name,
                    'source': 'injected_code',
                    'parent_sample': malware_sample.id

                })

                logging.info('[*] Submitting the code to dpa engine: {},{},{}'.format(outputpath, 'injected_code',
                                                                                      malware_sample.id))
                notes = {
                    'process_name': 'injected to {}'.format(process_name),
                    'whitelisted': False
                }

                send_dump_analysis_task(outputpath, 'injected_code', malware_sample.id,
                                        notes=notes)
                current_dump.report()

