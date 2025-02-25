import logging
import os
import subprocess
import sys
import time

from ruuvitag_sensor.adapters import BleCommunication

log = logging.getLogger(__name__)


class BleCommunicationNix(BleCommunication):
    """Bluetooth LE communication for Linux"""

    @staticmethod
    def start(bt_device=''):
        """
        Attributes:
           device (string): BLE device (default hci0)
        """
        # import ptyprocess here so as long as all implementations are in
        # the same file, all will work
        import ptyprocess

        if not bt_device:
            bt_device = 'hci0'

        log.info('Start receiving broadcasts (device %s)', bt_device)
        DEVNULL = subprocess.DEVNULL if sys.version_info >= (3, 3) else open(os.devnull, 'wb')

        def reset_ble_adapter():
            log.info("FYI: Calling a process with sudo: hciconfig %s reset", bt_device)
            return subprocess.call(
                'sudo hciconfig %s reset' % bt_device,
                shell=True,
                stdout=DEVNULL)

        def start_with_retry(func, try_count, interval, msg):
            retcode = func()
            if retcode != 0 and try_count > 0:
                log.info(msg)
                time.sleep(interval)
                return start_with_retry(
                    func, try_count - 1, interval + interval, msg)
            return retcode

        retcode = start_with_retry(
            reset_ble_adapter,
            3, 1,
            'Problem with hciconfig reset. Retry reset.')

        if retcode != 0:
            log.info('Problem with hciconfig reset. Exit.')
            exit(1)

        log.info("FYI: Spawning process with sudo: hcitool -i %s lescan2 --duplicates", bt_device)
        hcitool = ptyprocess.PtyProcess.spawn(
            ['sudo', '-n', 'hcitool', '-i', bt_device, 'lescan2', '--duplicates'])
        log.info("FYI: Spawning process with sudo: hcidump -i %s --raw", bt_device)
        hcidump = ptyprocess.PtyProcess.spawn(
            ['sudo', '-n', 'hcidump', '-i', bt_device, '--raw'])
        return (hcitool, hcidump)

    @staticmethod
    def stop(hcitool, hcidump):
        log.info('Stop receiving broadcasts')
        hcitool.close()
        hcidump.close()

    @staticmethod
    def get_lines(hcidump):
        data = None
        try:
            while True:
                line = hcidump.readline().decode()
                if line == '':
                    # EOF reached
                    raise Exception("EOF received from hcidump")

                line = line.strip()
                log.debug("Read line from hcidump: %s", line)
                if line.startswith('> '):
                    log.debug("Yielding %s", data)
                    yield data
                    data = line[2:].replace(' ', '')
                elif line.startswith('< '):
                    data = None
                else:
                    if data:
                        data += line.replace(' ', '')
        except KeyboardInterrupt:
            return
        except Exception as ex:
            log.info(ex)
            return

    def get_datas(self, blacklist=[], bt_device=''):
        procs = self.start(bt_device)

        data = None
        for line in self.get_lines(procs[1]):
            log.debug("Parsing line %s", line)
            try:
                # Make sure we're in upper case
                line = line.upper()
                # We're interested in LE meta events, sent by Ruuvitags.
                # Those start with "043E", followed by a length byte.

                if not line.startswith("043E"):
                    log.debug("Not a LE meta packet")
                    continue

                # The third byte is the parameter length, this should cover
                # the lenght of the entire packet, minus the first three bytes.
                # Note that the data is in hex format, so uses two chars per
                # byte
                plen = int(line[4:6], 16)
                if plen != (len(line) / 2) - 3:
                    log.debug("Invalid parameter length")
                    continue

                # The following two bytes should be "0201", indicating
                # 02  LE Advertising report
                # 01  1 report

                if line[6:10] != "0201":
                    log.debug("Not a Ruuvi advertisement")
                    continue

                # The next four bytes indicate whether the endpoint is
                # connectable or not, and whether the MAC address is random
                # or not. Different tags set different values here, so
                # ignore those.

                # The following 6 bytes are the MAC address of the sender,
                # in reverse order

                found_mac = line[14:26]
                reversed_mac = ''.join(
                    reversed([found_mac[i:i + 2] for i in range(0, len(found_mac), 2)]))
                mac = ':'.join(a + b for a, b in zip(reversed_mac[::2], reversed_mac[1::2]))
                if mac in blacklist:
                    log.debug('MAC blacklisted: %s', mac)
                    continue
                data = line[26:]
                log.debug("MAC: %s, data: %s", mac, data)
                yield (mac, data)
            except GeneratorExit:
                break
            except:
                continue

        self.stop(procs[0], procs[1])

    def get_data(self, mac, bt_device=''):
        data = None
        data_iter = self.get_datas([], bt_device)
        for data in data_iter:
            if mac == data[0]:
                log.info('Data found')
                data_iter.send(StopIteration)
                data = data[1]
                break

        return data
