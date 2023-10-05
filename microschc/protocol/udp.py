"""
UDP header parser

Parser for the UDP protocol header as defined in RFC768 [1].


[1] "RFC768: User Datagram Protocol", J. Postel
"""

from enum import Enum
from typing import Callable, Dict, Iterator, List, Tuple
from microschc.parser import HeaderParser, ParserError
from microschc.protocol import ComputeFunctionType
from microschc.protocol.ipv4 import IPV4_HEADER_ID, IPv4Fields
from microschc.rfc8724 import FieldDescriptor, HeaderDescriptor, RuleFieldDescriptor
from microschc.binary.buffer import Buffer, Padding
from microschc.protocol.ipv6 import IPv6_HEADER_ID, IPv6Fields

UDP_HEADER_ID = 'UDP'

class UDPFields(str, Enum):
    SOURCE_PORT         = f'{UDP_HEADER_ID}:Source Port'
    DESTINATION_PORT    = f'{UDP_HEADER_ID}:Destination Port'
    LENGTH              = f'{UDP_HEADER_ID}:Length'
    CHECKSUM            = f'{UDP_HEADER_ID}:Checksum'


class UDPParser(HeaderParser):

    def __init__(self) -> None:
        super().__init__(name=UDP_HEADER_ID)

    def parse(self, buffer:Buffer) -> HeaderDescriptor:
        """
         0      7 8     15 16    23 24    31
        +--------+--------+--------+--------+
        |     Source      |   Destination   |
        |      Port       |      Port       |
        +--------+--------+--------+--------+
        |                 |                 |
        |     Length      |    Checksum     |
        +--------+--------+--------+--------+
        |                                   |
        |          data octets ...          |
        +---------------- ... --------------|
        """

        if buffer.length < 64:
            raise ParserError(buffer, message=f'length too short: {buffer.length} < 64')

        # source port: 16 bits
        source_port:Buffer = buffer[0:16]

        # destination port: 16 bits
        destination_port:Buffer = buffer[16:32]

        # length: 16 bits
        length:Buffer = buffer[32:48]

        # checksum: 16 bits
        checksum:Buffer = buffer[48:64]

        header_descriptor:HeaderDescriptor = HeaderDescriptor(
            id=UDP_HEADER_ID,
            length=64,
            fields=[
                FieldDescriptor(id=UDPFields.SOURCE_PORT,       position=0, value=source_port),
                FieldDescriptor(id=UDPFields.DESTINATION_PORT,  position=0, value=destination_port),
                FieldDescriptor(id=UDPFields.LENGTH,            position=0, value=length),
                FieldDescriptor(id=UDPFields.CHECKSUM,          position=0, value=checksum),
            ]
        )
        return header_descriptor
        

def _compute_length(packet: Buffer, field_cursor: int, _: List[RuleFieldDescriptor], __: int) -> Buffer:
    # retrieve the buffer containing the UDP header and payload
    # 
    udp_header_and_payload: Buffer = packet[field_cursor-32:]
    length: int = udp_header_and_payload.length // 8 if udp_header_and_payload.length%8 == 0 else udp_header_and_payload.length // 8 + 1
    buffer: Buffer = Buffer(content=length.to_bytes(2, 'big'), length=16, padding=Padding.LEFT)
    return buffer

def _compute_checksum(packet: Buffer, field_cursor: int, decompressed_fields: List[Tuple[str, Buffer]], rule_field_position: int) -> Buffer:
    """
    Checksum is the 16-bit one's complement of the one's complement sum of a
    pseudo header of information from the IP header, the UDP header, and the
    data,  padded  with zero octets  at the end (if  necessary)  to  make  a
    multiple of two octets.

    For IPv4, the pseudo  header conceptually prefixed to the UDP header contains the
    source  address,  the destination  address,  the protocol,  and the  UDP
    length.   This information gives protection against misrouted datagrams.
    This checksum procedure is the same as is used in TCP.

                    0      7 8     15 16    23 24    31 
                    +--------+--------+--------+--------+
                    |          source address           |
                    +--------+--------+--------+--------+
                    |        destination address        |
                    +--------+--------+--------+--------+
                    |  zero  |protocol|   UDP length    |
                    +--------+--------+--------+--------+

    for IPv6, the pseudo header conceptually prefixed to the UDP header contains
    the source address, the destination address, the UDP Length, the Next Header
    on 4 octets, the UDP header and UDP payload.
            0                                                              31
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                                                               |
            +                                                               +
            |                                                               |
            +                         Source Address                        +
            |                                                               |
            +                                                               +
            |                                                               |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                                                               |
            +                                                               +
            |                                                               |
            +                      Destination Address                      +
            |                                                               |
            +                                                               +
            |                                                               |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                   Upper-Layer Packet Length                   |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            |                      zero                     |  Next Header  |
            +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+           


    If the computed  checksum  is zero,  it is transmitted  as all ones (the
    equivalent  in one's complement  arithmetic).   An all zero  transmitted
    checksum  value means that the transmitter  generated  no checksum  (for
    debugging or for higher level protocols that don't care).
    """

    # the UDP checksum computation is a tricky case, it depends on which IP version
    # is in use and requires building a pseudo header.
    # - first identify the encapsulating protocol, based on preceding field ids
    fields_ids: List[str] = [field_id for field_id, _ in decompressed_fields]
    fields_values: List[Buffer] = [field_value for _, field_value in decompressed_fields]
    
    #   - UDP checksum is the 4th field of UDP --> the last field of the preceding protocol
    #     is therefore at index (rule_field_position - 4)
    udp_checksum_position: int = rule_field_position
    preceding_protocol_last_position = udp_checksum_position-4
    preceding_protocol_last_field = fields_ids[preceding_protocol_last_position]

    # UDP header is 48 bits before the UDP checksum
    udp_header_and_payload: Buffer = packet[field_cursor-48:]
    udp_total_length: int = udp_header_and_payload.length // 8 if udp_header_and_payload.length%8 == 0 else udp_header_and_payload.length // 8 + 1

    fields_enumeration_reversed: Iterator[Tuple[int, str]] = enumerate(fields_ids[preceding_protocol_last_position:0:-1])

    
    if IPv6_HEADER_ID in preceding_protocol_last_field:
        # build up the pseudo header containing the Source Address, Destination Address, Protocol ID, UDP header + payload length
            # 0                                                              31
            # +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            # |                                                               |
            # +                                                               +
            # |                                                               |
            # +                         Source Address                        +
            # |                                                               |
            # +                                                               +
            # |                                                               |
            # +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            # |                                                               |
            # +                                                               +
            # |                                                               |
            # +                      Destination Address                      +
            # |                                                               |
            # +                                                               +
            # |                                                               |
            # +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            # |              length             |         Next Header         |
            # +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        ipv6_source_address_offset: int = next((offset for offset, field_id in fields_enumeration_reversed if field_id == IPv6Fields.SRC_ADDRESS))
        ipv6_source_address_position: int = preceding_protocol_last_position - ipv6_source_address_offset
        ipv6_source_address: Buffer = fields_values[ipv6_source_address_position]
        ipv6_destination_address: Buffer = fields_values[ipv6_source_address_position+1]
        pseudo_header_length: Buffer = Buffer(content=udp_total_length.to_bytes(4, 'big'), length=16)
        pseudo_header_protocol_id: Buffer = Buffer(content=b'\x00\x11', length=16)
        pseudo_header: Buffer = ipv6_source_address + ipv6_destination_address + pseudo_header_protocol_id + pseudo_header_length
        
    elif IPV4_HEADER_ID in preceding_protocol_last_field:
        # build up the pseudo header containing the Source Address, Destination Address, Protocol ID, UDP header + payload length
                    #  0      7 8     15 16    23 24    31 
                    # +--------+--------+--------+--------+
                    # |          source address           |
                    # +--------+--------+--------+--------+
                    # |        destination address        |
                    # +--------+--------+--------+--------+
                    # |  zero  |protocol|   UDP length    |
                    # +--------+--------+--------+--------+

        fields_enumeration_reversed: Iterator[Tuple[int, str]] = enumerate(fields_ids[preceding_protocol_last_position:0:-1])
        ipv4_source_address_offset: int = next(offset for offset, field_id in fields_enumeration_reversed if field_id == IPv4Fields.SRC_ADDRESS)
        ipv4_source_address_position: int = preceding_protocol_last_position - ipv4_source_address_offset
        ipv4_source_address: Buffer = fields_values[ipv4_source_address_position]
        ipv4_destination_address: Buffer = fields_values[ipv4_source_address_position+1]
        pseudo_header_protocol_id: Buffer = Buffer(content=b'\x00\x11', length=16)
        pseudo_header_length: Buffer = Buffer(content=udp_total_length.to_bytes(2, 'big'), length=16)
        pseudo_header: Buffer = ipv4_source_address + ipv4_destination_address  + pseudo_header_protocol_id + pseudo_header_length

    checksum_value: int = 0
    pseudo_header_checksum: int = 0
    # compute the sum of the 2-bytes chunks of the pseudo header + UDP checksum + payload
    for chunk in pseudo_header.chunks(length=16):
        pseudo_header_checksum += chunk.value(type='unsigned int')
        carry = pseudo_header_checksum >> 16
        pseudo_header_checksum = (pseudo_header_checksum + carry) & 0xffff 

    udp_header_and_payload_checksum: int = 0
    for chunk in udp_header_and_payload.chunks(length=16, padding=True):
        udp_header_and_payload_checksum += chunk.value(type='unsigned int')
        carry = udp_header_and_payload_checksum >> 16
        udp_header_and_payload_checksum = (udp_header_and_payload_checksum + carry) & 0xffff

    checksum_value: int = pseudo_header_checksum + udp_header_and_payload_checksum
    carry = checksum_value >> 16
    checksum_value = (checksum_value+carry) & 0xffff
    
    checksum_value = ~checksum_value & 0xffff

    # if checksum is 0x0000 return 0xffff
    checksum_value = 0xffff if checksum_value == 0x0000 else checksum_value

    
    checksum_buffer: Buffer = Buffer(content=checksum_value.to_bytes(2, 'big'), length=16)
    return checksum_buffer



UDPComputeFunctions: Dict[str, ComputeFunctionType] = {
    UDPFields.LENGTH: _compute_length,
    UDPFields.CHECKSUM: _compute_checksum,
}
    
