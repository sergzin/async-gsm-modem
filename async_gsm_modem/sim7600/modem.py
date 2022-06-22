import asyncio.exceptions

from ..base.modem import ATModem
from ..base.response import Response
from ..base.command import Command, ExtendedCommand
from ..base.pdu import encodeSmsSubmitPdu, encodeGsm7
from .sms import SMS
from .exceptions import *
from .constants import STATUS_MAP, STATUS_MAP_R, DELETE_FLAG, UNSOLICITED_RESULT_CODES, CALL_DIR, CALL_TYPE, CALL_MODE, \
    CALL_STATE, ERROR_CODES
from .call import VoiceCall
from typing import List, Type, Optional
from .info import ProductInfo
import logging
from io import StringIO
from smspdudecoder.fields import SMSDeliver


class Modem(ATModem):

    def __init__(self, device: str, baud_rate: int):
        super().__init__(device, baud_rate, UNSOLICITED_RESULT_CODES, error_codes=ERROR_CODES)

        self.logger = logging.getLogger('Sim7600')

    async def ping(self):
        response = await self.send_command(Command(b'AT'))
        return response == Response([])

    async def product_info(self) -> ProductInfo:
        response = await self.send_command(Command(b'ATI'))
        response = [r.decode() for r in response]
        return ProductInfo(
            manufacturer=response[0],
            model=response[1],
            revision=response[2].replace('Revision: ', '')
        )

    async def imei(self):
        response = await self.send_command(Command(b'AT+GSN'))
        return response[0].decode()

    async def imsi(self):
        response = await self.send_command(Command(b'AT+CIMI'))
        return response[0].decode()

    async def iccid(self):
        response = await self.send_command(Command(b'AT+CICCID'))
        return response[0].decode().replace('+ICCID:: ', '')

    async def number(self):
        response = await self.send_command(Command(b'AT+CNUM'))
        response = response[0].decode()
        number = response.split(',')[1].replace('"', '')
        return number

    async def network_info(self):
        response = await self.send_command(ExtendedCommand(b'AT+CPSI').read())
        response = response[0].decode().replace('+CPSI: ', '')
        return response

    async def network_registration(self):
        response = await self.send_command(Command(b'AT+CREG?'))
        response = response[0].decode().replace('+CREG: ', '')
        return response

    async def signal_quality(self):
        response = await self.send_command(Command(b'AT+CSQ'))
        response = response[0].decode().replace('+CSQ: ', '')
        return response

    def parse_message(self, index, status, alpha, length, pdu) -> SMS:
        data = SMSDeliver.decode(StringIO(pdu.decode()))

        text = str(data['user_data']['data'])
        from_number = data['sender']['number']
        date = data['scts']

        return SMS(
            index=index,
            status=status,
            alpha=alpha,
            length=length,
            pdu=pdu,
            text=text,
            from_number=from_number,
            date=date
        )

    async def read_message(self, index: int) -> Optional[SMS]:
        try:
            command = ExtendedCommand(b'AT+CMGR').write(str(index).encode())
            response = await self.send_command(command)
        except Exception as e:
            self.logger.error('Failed to read message', exc_info=True)
            raise ReadMessageError from e

        if response:
            status, alpha, length = response[0].replace(b'+CMGR: ', b'').split(b',')
            pdu = response[1]

            return self.parse_message(index, status, alpha, length, pdu)
        else:
            return

    async def send_message(self, to_number: str, text: str, timeout: int = 5) -> List:
        try:
            pdus = encodeSmsSubmitPdu(to_number, text)
            message_references = []
            async with self.write_lock:
                for pdu in pdus:
                    length = str(pdu[1]).encode()
                    command = ExtendedCommand(b'AT+CMGS').write(length)
                    await self.write(command)
                    await self.read(seperator=b'> ')  # read out and discard prompt
                    command = ExtendedCommand(pdu[0].hex().upper().encode()).execute()
                    await self.write(command, terminator=chr(26).encode())  # send pdu with CTRL-Z terminator
                    response = await self.read_response(expected_response=b'+CMGS', timeout=timeout)
                    self.at_logger.debug(response)
                    message_references.append(response[0].replace(b'+CMGS: ', b'').decode())
            return message_references
        except Exception as e:
            self.logger.error('Failed to send message', exc_info=True)
            raise SendMessageError from e

    async def list_messages(self, status: str = 'ALL') -> List[SMS]:
        if status not in STATUS_MAP:
            raise ValueError(f'Invalid status {status}: {tuple(STATUS_MAP.keys())}')
        status = STATUS_MAP[status]

        try:
            command = ExtendedCommand(b'AT+CMGL').write(status)
            response = await self.send_command(command)

            if len(response) % 2 > 0:
                raise ValueError(f'Expecting even number of parts in response: {response}')

            messages = []
            for n in range(len(response) // 2):
                try:
                    message_info = response[n * 2]
                    index, status, alpha, length = message_info.replace(b'+CMGL: ', b'').split(b',')
                    pdu = response[(n * 2) + 1]
                    message = self.parse_message(index, status, alpha, length, pdu)
                    messages.append(message)
                except:
                    self.logger.error(f'Failed to parse message: {response[(n * 2):(n * 2) + 1]}', exc_info=True)

            return messages
        except Exception as e:
            self.logger.error('Failed to list messages', exc_info=True)
            raise ReadMessageError from e

    async def delete_message(self, index: int):
        command = ExtendedCommand(b'AT+CMGD').write(str(index).encode())
        await self.send_command(command)
        self.logger.debug(f'Deleted message at index {index}')

    async def delete_messages(self, del_flag: str = 'ALL'):
        assert del_flag in DELETE_FLAG, \
            KeyError(f'Invalid delete flag {del_flag}: {tuple(DELETE_FLAG.keys())}')

        command = ExtendedCommand(b'AT+CMGD').write(b'0', DELETE_FLAG[del_flag])
        await self.send_command(command)
        self.logger.debug(f'Deleted {del_flag} messages')

    async def call_hangup(self):
        command = Command(b'AT+CHUP')
        return await self.send_command(command)

    async def call_answer(self):
        command = Command(b'ATA')
        await self.send_command(command)

    async def call_list_current(self):
        """
        +CLCC: <id2>,<dir>,<stat>,<mode>,<mpty>[,<number>,<type>[,<alpha>]]
        """
        command = ExtendedCommand(b'AT+CLCC').execute()
        resp = await self.send_command(command)
        if resp:
            print(resp)
            idx, direction, stat, mode, conf, *rest = resp[0].replace(b'+CLCC:', b'').strip().split(b',')
            return VoiceCall(
                index=int(idx),
                direction=CALL_DIR[direction],
                state=CALL_STATE[stat],
                mode=CALL_MODE[mode],
                conf=True if conf == b'1' else False,
                number=rest[0] if rest else None,
                type=CALL_TYPE[rest[1]] if len(rest) > 1 else None
            )

    async def call_dial_number(self, number: str):
        command = Command(b'ATD' + number.encode() + b';')
        await self.send_command(command)

    async def change_disk(self, disk_letter: bytes):
        command = ExtendedCommand(b'AT+FSCD').write(disk_letter)
        await self.send_command(command)

    async def list_files(self):
        command = ExtendedCommand(b'AT+FSLS').execute()
        return await self.send_command(command)

    async def play_recording(self, path: bytes):
        """
        Play recording from flash to remote party. Only during active call.
        AT+CCMXPLAY=<filename>[,<play_path>][,<repeat>]
        <play_path> Play to local or to remote. Default 0
        0 – local
        1 – remote
        <repeat> How much times can be played. Default 0
        <filename> The location and name of wav file.
        """
        command = ExtendedCommand(b'AT+CCMXPLAY').write(path, b'1')
        await self.send_command(command, response_terminator=b'+AUDIOSTATE: audio play stop', timeout=300)
