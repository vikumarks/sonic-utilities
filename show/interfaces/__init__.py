import json
import os

import subprocess
import click
from utilities_common import constants
import utilities_common.cli as clicommon
import utilities_common.multi_asic as multi_asic_util
from natsort import natsorted
from tabulate import tabulate
from sonic_py_common import multi_asic
from sonic_py_common import device_info
from swsscommon.swsscommon import ConfigDBConnector, SonicV2Connector
from portconfig import get_child_ports
import sonic_platform_base.sonic_sfp.sfputilhelper

from . import portchannel
from collections import OrderedDict

HWSKU_JSON = 'hwsku.json'

REDIS_HOSTIP = "127.0.0.1"

# Read given JSON file
def readJsonFile(fileName):

    try:
        with open(fileName) as f:
            result = json.load(f)
    except FileNotFoundError as e:
        click.echo("{}".format(str(e)), err=True)
        raise click.Abort()
    except json.decoder.JSONDecodeError as e:
        click.echo("Invalid JSON file format('{}')\n{}".format(fileName, str(e)), err=True)
        raise click.Abort()
    except Exception as e:
        click.echo("{}\n{}".format(type(e), str(e)), err=True)
        raise click.Abort()
    return result


def try_convert_interfacename_from_alias(ctx, interfacename):
    """try to convert interface name from alias"""

    if clicommon.get_interface_naming_mode() == "alias":
        alias = interfacename
        interfacename = clicommon.InterfaceAliasConverter().alias_to_name(alias)
        # TODO: ideally alias_to_name should return None when it cannot find
        # the port name for the alias
        if interfacename == alias:
            ctx.fail("cannot find interface name for alias {}".format(alias))

    return interfacename

#
# 'interfaces' group ("show interfaces ...")
#


@click.group(cls=clicommon.AliasedGroup)
def interfaces():
    """Show details of the network interfaces"""
    pass


# 'alias' subcommand ("show interfaces alias")
@interfaces.command()
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
def alias(interfacename, namespace, display):
    """Show Interface Name/Alias Mapping"""

    ctx = click.get_current_context()

    port_dict = multi_asic.get_port_table(namespace=namespace)

    header = ['Name', 'Alias']
    body = []

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        # If we're given an interface name, output name and alias for that interface only
        if interfacename in port_dict:
            if 'alias' in port_dict[interfacename]:
                body.append([interfacename, port_dict[interfacename]['alias']])
            else:
                body.append([interfacename, interfacename])
        else:
            ctx.fail("Invalid interface name {}".format(interfacename))
    else:
        # Output name and alias for all interfaces
        for port_name in natsorted(list(port_dict.keys())):
            if ((display == multi_asic_util.constants.DISPLAY_EXTERNAL) and
                ('role' in port_dict[port_name]) and
                    (port_dict[port_name]['role'] is multi_asic.INTERNAL_PORT)):
                continue
            if 'alias' in port_dict[port_name]:
                body.append([port_name, port_dict[port_name]['alias']])
            else:
                body.append([port_name, port_name])

    click.echo(tabulate(body, header))


@interfaces.command()
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def description(interfacename, namespace, display, verbose):
    """Show interface status, protocol and description"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'description']

    # ignore the display option when interface name is passed
    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]
    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)

# 'naming_mode' subcommand ("show interfaces naming_mode")


@interfaces.command('naming_mode')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def naming_mode(verbose):
    """Show interface naming_mode status"""

    click.echo(clicommon.get_interface_naming_mode())


@interfaces.command()
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def status(interfacename, namespace, display, verbose):
    """Show Interface status information"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'status']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]

    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@interfaces.command()
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def tpid(interfacename, namespace, display, verbose):
    """Show Interface tpid information"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'tpid']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]
    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


#
# 'breakout' group ###
#
@interfaces.group(invoke_without_command=True)
@click.pass_context
def breakout(ctx):
    """Show Breakout Mode information by interfaces"""
    # Reading data from Redis configDb
    config_db = ConfigDBConnector()
    config_db.connect()
    ctx.obj = {'db': config_db}

    try:
        cur_brkout_tbl = config_db.get_table('BREAKOUT_CFG')
    except Exception as e:
        click.echo("Breakout table is not present in Config DB")
        raise click.Abort()

    if ctx.invoked_subcommand is None:
        # Get port capability from platform and hwsku related files
        hwsku_path = device_info.get_path_to_hwsku_dir()
        platform_file = device_info.get_path_to_port_config_file()
        platform_dict = readJsonFile(platform_file)['interfaces']
        hwsku_file = os.path.join(hwsku_path, HWSKU_JSON)
        hwsku_dict = readJsonFile(hwsku_file)['interfaces']

        if not platform_dict or not hwsku_dict:
            click.echo("Can not load port config from {} or {} file".format(platform_file, hwsku_file))
            raise click.Abort()

        for port_name in platform_dict:
            # Check whether port is available in `BREAKOUT_CFG` table or not
            if  port_name not in cur_brkout_tbl:
                continue
            cur_brkout_mode = cur_brkout_tbl[port_name]["brkout_mode"]

            # Update deafult breakout mode and current breakout mode to platform_dict
            platform_dict[port_name].update(hwsku_dict[port_name])
            platform_dict[port_name]["Current Breakout Mode"] = cur_brkout_mode

            # List all the child ports if present
            child_port_dict = get_child_ports(port_name, cur_brkout_mode, platform_file)
            if not child_port_dict:
                click.echo("Cannot find ports from {} file ".format(platform_file))
                raise click.Abort()

            child_ports = natsorted(list(child_port_dict.keys()))

            children, speeds = [], []
            # Update portname and speed of child ports if present
            for port in child_ports:
                speed = config_db.get_entry('PORT', port).get('speed')
                if speed is not None:
                    speeds.append(str(int(speed)//1000)+'G')
                    children.append(port)

            platform_dict[port_name]["child ports"] = ",".join(children)
            platform_dict[port_name]["child port speeds"] = ",".join(speeds)

        # Sorted keys by name in natural sort Order for human readability

        parsed = OrderedDict((k, platform_dict[k]) for k in natsorted(list(platform_dict.keys())))
        click.echo(json.dumps(parsed, indent=4))


# 'breakout current-mode' subcommand ("show interfaces breakout current-mode")
@breakout.command('current-mode')
@click.argument('interface', metavar='<interface_name>', required=False, type=str)
@click.pass_context
def currrent_mode(ctx, interface):
    """Show current Breakout mode Info by interface(s)"""

    config_db = ctx.obj['db']

    header = ['Interface', 'Current Breakout Mode']
    body = []

    try:
        cur_brkout_tbl = config_db.get_table('BREAKOUT_CFG')
    except Exception as e:
        click.echo("Breakout table is not present in Config DB")
        raise click.Abort()

    # Show current Breakout Mode of user prompted interface
    if interface is not None:
        # Check whether interface is available in `BREAKOUT_CFG` table or not
        if interface in cur_brkout_tbl:
            body.append([interface, str(cur_brkout_tbl[interface]['brkout_mode'])])
        else:
            body.append([interface, "Not Available"])
        click.echo(tabulate(body, header, tablefmt="grid"))
        return

    # Show current Breakout Mode for all interfaces
    for name in natsorted(list(cur_brkout_tbl.keys())):
        body.append([name, str(cur_brkout_tbl[name]['brkout_mode'])])
    click.echo(tabulate(body, header, tablefmt="grid"))


#
# 'neighbor' group ###
#
@interfaces.group(cls=clicommon.AliasedGroup)
def neighbor():
    """Show neighbor related information"""
    pass


# 'expected' subcommand ("show interface neighbor expected")
@neighbor.command()
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_option_namespace
@clicommon.pass_db
def expected(db, interfacename, namespace):
    """Show expected neighbor information by interfaces"""

    if not namespace:
        namespace = multi_asic_util.constants.DEFAULT_NAMESPACE

    neighbor_dict = db.cfgdb_clients[namespace].get_table("DEVICE_NEIGHBOR")
    if neighbor_dict is None:
        click.echo("DEVICE_NEIGHBOR information is not present.")
        return

    neighbor_metadata_dict = db.cfgdb_clients[namespace].get_table("DEVICE_NEIGHBOR_METADATA")
    if neighbor_metadata_dict is None:
        click.echo("DEVICE_NEIGHBOR_METADATA information is not present.")
        return

    for port in natsorted(list(neighbor_dict.keys())):
        temp_port = port
        if clicommon.get_interface_naming_mode() == "alias":
            port = clicommon.InterfaceAliasConverter().name_to_alias(port)
            neighbor_dict[port] = neighbor_dict.pop(temp_port)
    header = ['LocalPort', 'Neighbor', 'NeighborPort',
              'NeighborLoopback', 'NeighborMgmt', 'NeighborType']
    body = []
    if interfacename:
        try:
            device = neighbor_dict[interfacename]['name']
            body.append([interfacename,
                         device,
                         neighbor_dict[interfacename]['port'],
                         neighbor_metadata_dict[device]['lo_addr'] if 'lo_addr'
                         in neighbor_metadata_dict[device] else 'None',
                         neighbor_metadata_dict[device]['mgmt_addr'] if 'mgmt_addr'
                         in neighbor_metadata_dict[device] else 'None',
                         neighbor_metadata_dict[device]['type'] if 'type'
                         in neighbor_metadata_dict[device] else 'None'])
        except KeyError:
            click.echo("No neighbor information available for interface {}".format(interfacename))
            return
    else:
        for port in natsorted(list(neighbor_dict.keys())):
            try:
                device = neighbor_dict[port]['name']
                body.append([port,
                             device,
                             neighbor_dict[port]['port'],
                             neighbor_metadata_dict[device]['lo_addr'] if 'lo_addr'
                             in neighbor_metadata_dict[device] else 'None',
                             neighbor_metadata_dict[device]['mgmt_addr'] if 'mgmt_addr'
                             in neighbor_metadata_dict[device] else 'None',
                             neighbor_metadata_dict[device]['type'] if 'type'
                             in neighbor_metadata_dict[device] else 'None'])
            except KeyError:
                pass

    click.echo(tabulate(body, header))


@interfaces.command()
@click.argument('interfacename', required=False)
@click.option('--namespace', '-n', 'namespace', default=None,
              type=str, show_default=True, help='Namespace name or all',
              callback=multi_asic_util.multi_asic_namespace_validation_callback)
@click.option('--display', '-d', 'display', default=None, show_default=False,
              type=str, help='all|frontend')
@click.pass_context

def mpls(ctx, interfacename, namespace, display):
    """Show Interface MPLS status"""
    # Edge case: Force show frontend interfaces on single asic
    if not (multi_asic.is_multi_asic()):
       if (display == 'frontend' or display == 'all' or display is None):
           display = None
       else:
           print("Error: Invalid display option command for single asic")
           return

    display = "all" if interfacename else display
    masic = multi_asic_util.MultiAsic(display_option=display, namespace_option=namespace)
    ns_list = masic.get_ns_list_based_on_options()
    intfs_data = {}
    intf_found = False

    for ns in ns_list:

        appl_db = multi_asic.connect_to_all_dbs_for_ns(namespace=ns)

        if interfacename is not None:
            interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        # Fetching data from appl_db for intfs
        keys = appl_db.keys(appl_db.APPL_DB, "INTF_TABLE:*")
        for key in keys if keys else []:
            tokens = key.split(":")
            ifname = tokens[1]
            # Skip INTF_TABLE entries with address information
            if len(tokens) != 2:
                continue

            if (interfacename is not None):
                if (interfacename != ifname):
                    continue

                intf_found = True

            if (display != "all"):
                if ("Loopback" in ifname):
                    continue

                if ifname.startswith("Ethernet") and multi_asic.is_port_internal(ifname, ns):
                    continue

                if ifname.startswith("PortChannel") and multi_asic.is_port_channel_internal(ifname, ns):
                    continue

            mpls_intf = appl_db.get_all(appl_db.APPL_DB, key)

            if 'mpls' not in mpls_intf or mpls_intf['mpls'] == 'disable':
                intfs_data.update({ifname: 'disable'})
            else:
                intfs_data.update({ifname: mpls_intf['mpls']})

    # Check if interface is valid
    if (interfacename is not None and not intf_found):
        ctx.fail('interface {} doesn`t exist'.format(interfacename))

    header = ['Interface', 'MPLS State']
    body = []

    # Output name and alias for all interfaces
    for intf_name in natsorted(list(intfs_data.keys())):
        if clicommon.get_interface_naming_mode() == "alias":
            alias = clicommon.InterfaceAliasConverter().name_to_alias(intf_name)
            body.append([alias, intfs_data[intf_name]])
        else:
            body.append([intf_name, intfs_data[intf_name]])

    click.echo(tabulate(body, header))


interfaces.add_command(portchannel.portchannel)



@interfaces.command()
@click.argument('interfacename', required=False)
@click.pass_context
def flap(ctx, interfacename):
    """Show Interface Flap Information <interfacename>"""

    namespace = ''  # Default namespace
    port_dict = multi_asic.get_port_table(namespace=namespace)

    # If interfacename is given, validate it
    if interfacename:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)
        if interfacename not in port_dict:
            ctx.fail("Invalid interface name {}".format(interfacename))

    db = SonicV2Connector(host=REDIS_HOSTIP)
    db.connect(db.APPL_DB)

    # Prepare the table headers and body
    header = [
        'Interface',
        'Flap Count',
        'Admin',
        'Oper',
        'Link Down TimeStamp(UTC)',
        'Link Up TimeStamp(UTC)'
    ]
    body = []

    # Loop through all ports or the specified port
    ports = [interfacename] if interfacename else natsorted(list(port_dict.keys()))

    for port in ports:
        port_data = db.get_all(db.APPL_DB, f'PORT_TABLE:{port}') or {}

        flap_count = port_data.get('flap_count', 'Never')
        admin_status = port_data.get('admin_status', 'Unknown').capitalize()
        oper_status = port_data.get('oper_status', 'Unknown').capitalize()

        # Get timestamps and convert them to UTC format if possible
        last_up_time = port_data.get('last_up_time', 'Never')
        last_down_time = port_data.get('last_down_time', 'Never')

        # Format output row
        row = [
            port,
            flap_count,
            admin_status,
            oper_status,
            f"{last_down_time}" if last_down_time != 'Never' else 'Never',
            f"{last_up_time}" if last_up_time != 'Never' else 'Never'
        ]

        body.append(row)

    # Sort the body by interface name for consistent display
    body = natsorted(body, key=lambda x: x[0])

    # Display the formatted table
    click.echo(tabulate(body, header))

    db.close(db.APPL_DB)


def get_all_port_errors(interfacename):

    port_operr_table = {}
    db = SonicV2Connector(host=REDIS_HOSTIP)
    db.connect(db.STATE_DB)

    # Retrieve the errors data from the PORT_OPERR_TABLE
    port_operr_table = db.get_all(db.STATE_DB, 'PORT_OPERR_TABLE|{}'.format(interfacename))
    db.close(db.STATE_DB)

    return port_operr_table


@interfaces.command()
@click.argument('interfacename', required=True)
@click.pass_context
def errors(ctx, interfacename):
    """Show Interface Erorrs <interfacename>"""
    # Try to convert interface name from alias
    interfacename = try_convert_interfacename_from_alias(click.get_current_context(), interfacename)

    port_operr_table = get_all_port_errors(interfacename)

    # Define a list of all potential errors
    ALL_PORT_ERRORS = [
        "oper_error_status",
        "mac_local_fault",
        "mac_remote_fault",
        "fec_sync_loss",
        "fec_alignment_loss",
        "high_ser_error",
        "high_ber_error",
        "data_unit_crc_error",
        "data_unit_misalignment_error",
        "signal_local_error",
        "crc_rate",
        "data_unit_size",
        "code_group_error",
        "no_rx_reachability"
    ]

    # Prepare the table headers and body
    header = ['Port Errors', 'Count', 'Last timestamp(UTC)']
    body = []

    # Populate the table body with all errors, defaulting missing ones to 0 and Never
    for error in ALL_PORT_ERRORS:
        count_key = f"{error}_count"
        time_key = f"{error}_time"

        if port_operr_table is not None:
            count = port_operr_table.get(count_key, "0")  # Default count to '0'
            timestamp = port_operr_table.get(time_key, "Never")  # Default timestamp to 'Never'
        else:
            count = "0"
            timestamp = "Never"

        # Add to table
        body.append([error.replace('_', ' '), count, timestamp])

    # Sort the body for consistent display
    body.sort(key=lambda x: x[0])

    # Display the formatted table
    click.echo(tabulate(body, header))

#
# transceiver group (show interfaces trasceiver ...)
#
@interfaces.group(cls=clicommon.AliasedGroup)
def transceiver():
    """Show SFP Transceiver information"""
    pass


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('-d', '--dom', 'dump_dom', is_flag=True, help="Also display Digital Optical Monitoring (DOM) data")
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def eeprom(interfacename, dump_dom, namespace, verbose):
    """Show interface transceiver EEPROM information"""

    ctx = click.get_current_context()

    cmd = ['sfpshow', 'eeprom']

    if dump_dom:
        cmd += ["--dom"]

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def pm(interfacename, namespace, verbose):
    """Show interface transceiver performance monitoring information"""

    ctx = click.get_current_context()

    cmd = ['sfpshow', 'pm']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(
            ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command('status')  # 'status' is the actual sub-command name under 'transceiver' command
@click.argument('interfacename', required=False)
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def transceiver_status(interfacename, namespace, verbose):
    """Show interface transceiver status information"""

    ctx = click.get_current_context()

    cmd = ['sfpshow', 'status']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(
            ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def info(interfacename, namespace, verbose):
    """Show interface transceiver information"""

    ctx = click.get_current_context()

    cmd = ['sfpshow', 'info']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def lpmode(interfacename, verbose):
    """Show interface transceiver low-power mode status"""

    ctx = click.get_current_context()

    cmd = ['sudo', 'sfputil', 'show', 'lpmode']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@clicommon.pass_db
def presence(db, interfacename, namespace, verbose):
    """Show interface transceiver presence"""

    ctx = click.get_current_context()

    cmd = ['sfpshow', 'presence']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


@transceiver.command()
@click.argument('interfacename', required=False)
@click.option('--fetch-from-hardware', '-hw', 'fetch_from_hardware', is_flag=True, default=False)
@click.option('--namespace', '-n', 'namespace', default=None, show_default=True,
              type=click.Choice(multi_asic_util.multi_asic_ns_choices()), help='Namespace name or all')
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@clicommon.pass_db
def error_status(db, interfacename, fetch_from_hardware, namespace, verbose):
    """ Show transceiver error-status """

    ctx = click.get_current_context()

    cmd = ['sudo', 'sfputil', 'show', 'error-status']

    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-p', str(interfacename)]

    if fetch_from_hardware:
        cmd += ["-hw"]
    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


#
# counters group ("show interfaces counters ...")
#

@interfaces.group(invoke_without_command=True)
@multi_asic_util.multi_asic_click_options
@click.option('-i', '--interface', help="Filter by interface name")
@click.option('-a', '--printall', is_flag=True, help="Show all counters")
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@click.option('--nonzero', is_flag=True, help="Only display non zero counters")
@click.pass_context
def counters(ctx, namespace, display, interface, printall, period, json_fmt, verbose, nonzero):
    """Show interface counters"""

    if ctx.invoked_subcommand is None:
        cmd = ["portstat"]

        if printall:
            cmd += ["-a"]
        if period is not None:
            cmd += ['-p', str(period)]
        if interface is not None:
            interface = try_convert_interfacename_from_alias(ctx, interface)
            cmd += ['-i', str(interface)]
        else:
            cmd += ['-s', str(display)]
        if namespace is not None:
            cmd += ['-n', str(namespace)]
        if json_fmt:
            cmd += ['-j']
        if nonzero:
            cmd += ['-nz']

        clicommon.run_command(cmd, display_cmd=verbose)


# 'errors' subcommand ("show interfaces counters errors")
@counters.command()
@multi_asic_util.multi_asic_click_options
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def errors(namespace, display, period, json_fmt, verbose):  # noqa: F811
    """Show interface counters errors"""

    cmd = ['portstat', '-e']

    if period is not None:
        cmd += ['-p', str(period)]

    cmd += ['-s', str(display)]
    if namespace is not None:
        cmd += ['-n', str(namespace)]

    if json_fmt:
        cmd += ['-j']

    clicommon.run_command(cmd, display_cmd=verbose)


# 'fec-stats' subcommand ("show interfaces counters errors")
@counters.command('fec-stats')
@multi_asic_util.multi_asic_click_options
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@click.option('--nonzero', is_flag=True, help="Only display non zero counters")
def fec_stats(namespace, display, period, json_fmt, verbose, nonzero):
    """Show interface counters fec-stats"""

    cmd = ['portstat', '-f']

    if period is not None:
        cmd += ['-p', str(period)]

    cmd += ['-s', str(display)]
    if namespace is not None:
        cmd += ['-n', str(namespace)]

    if json_fmt:
        cmd += ['-j']

    if nonzero:
        cmd += ['-nz']

    clicommon.run_command(cmd, display_cmd=verbose)


def get_port_oid_mapping(db, namespace):
    ''' Returns dictionary of all ports interfaces and their OIDs. '''
    port_oid_map = db.db_clients[namespace].get_all(db.db.COUNTERS_DB, 'COUNTERS_PORT_NAME_MAP')
    return port_oid_map


def fetch_fec_histogram(db, namespace, port_oid_map, target_port):
    ''' Fetch and display FEC histogram for the given port. '''

    if target_port not in port_oid_map:
        click.echo('Port {} not found in COUNTERS_PORT_NAME_MAP'.format(target_port), err=True)
        raise click.Abort()

    port_oid = port_oid_map[target_port]
    asic_db_kvp = db.db_clients[namespace].get_all(db.db.COUNTERS_DB, 'COUNTERS:{}'.format(port_oid))

    if asic_db_kvp is not None:

        fec_errors = {f'BIN{i}': asic_db_kvp.get
                      (f'SAI_PORT_STAT_IF_IN_FEC_CODEWORD_ERRORS_S{i}', '0') for i in range(16)}

        # Prepare the data for tabulation
        table_data = [(bin_label, error_value) for bin_label, error_value in fec_errors.items()]

        # Define headers
        headers = ["Symbol Errors Per Codeword", "Codewords"]

        # Print FEC histogram using tabulate
        click.echo(tabulate(table_data, headers=headers))
    else:
        click.echo('No kvp found in ASIC DB for port {}, exiting'.format(target_port), err=True)
        raise click.Abort()



# 'fec-histogram' subcommand ("show interfaces counters fec-histogram")
@counters.command('fec-histogram')
@multi_asic_util.multi_asic_click_options
@click.argument('interfacename', required=True)
@clicommon.pass_db
def fec_histogram(db, interfacename, namespace, display):
    """Show interface counters fec-histogram"""

    if namespace is None:
        namespace = constants.DEFAULT_NAMESPACE

    port_oid_map = get_port_oid_mapping(db, namespace)

    # Try to convert interface name from alias
    interfacename = try_convert_interfacename_from_alias(click.get_current_context(), interfacename)

    # Fetch and display the FEC histogram
    fetch_fec_histogram(db, namespace, port_oid_map, interfacename)


# 'rates' subcommand ("show interfaces counters rates")
@counters.command()
@multi_asic_util.multi_asic_click_options
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def rates(namespace, display, period, json_fmt, verbose):
    """Show interface counters rates"""

    cmd = ['portstat', '-R']

    if period is not None:
        cmd += ['-p', str(period)]
    cmd += ['-s', str(display)]
    if namespace is not None:
        cmd += ['-n', str(namespace)]
    if json_fmt:
        cmd += ['-j']

    clicommon.run_command(cmd, display_cmd=verbose)


# 'counters' subcommand ("show interfaces counters rif")
@counters.command()
@click.argument('interface', metavar='[INTERFACE_NAME]', required=False, type=str)
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@click.pass_context
def rif(ctx, interface, period, json_fmt, verbose):
    """Show interface counters rif"""

    cmd = ['intfstat']

    if period is not None:
        cmd += ['-p', str(period)]
    if interface is not None:
        interface = try_convert_interfacename_from_alias(ctx, interface)
        cmd += ['-i', str(interface)]
    if json_fmt:
        cmd += ['-j']

    clicommon.run_command(cmd, display_cmd=verbose)


# 'counters' subcommand ("show interfaces counters trim")
@counters.command()
@click.argument('interface', metavar='[INTERFACE_NAME]', required=False, type=str)
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('-j', '--json', 'json_fmt', is_flag=True, help="Print in JSON format")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@click.pass_context
def trim(ctx, interface, period, json_fmt, verbose):
    """Show interface counters trim"""

    cmd = ['portstat', '-T']

    if interface is not None:
        interface = try_convert_interfacename_from_alias(ctx, interface)
        cmd += ['-i', str(interface)]
    if period is not None:
        cmd += ['-p', str(period)]
    if json_fmt:
        cmd += ['-j']

    clicommon.run_command(cmd, display_cmd=verbose)


# 'counters' subcommand ("show interfaces counters detailed")
@counters.command()
@click.argument('interface', metavar='<INTERFACE_NAME>', required=True, type=str)
@click.option('-p', '--period', type=click.INT, help="Display statistics over a specified period (in seconds)")
@click.option('--verbose', is_flag=True, help="Enable verbose output")
@click.pass_context
def detailed(ctx, interface, period, verbose):
    """Show interface counters detailed"""

    cmd = ['portstat', '-l']

    if period is not None:
        cmd += ['-p', str(period)]
    if interface is not None:
        interface = try_convert_interfacename_from_alias(ctx, interface)
        cmd += ['-i', str(interface)]

    clicommon.run_command(cmd, display_cmd=verbose)


#
# autoneg group (show interfaces autoneg ...)
#
@interfaces.group(name='autoneg', cls=clicommon.AliasedGroup)
def autoneg():
    """Show interface autoneg information"""
    pass


# 'autoneg status' subcommand ("show interfaces autoneg status")
@autoneg.command(name='status')
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def autoneg_status(interfacename, namespace, display, verbose):
    """Show interface autoneg status"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'autoneg']

    # ignore the display option when interface name is passed
    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]
    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)


#
# link-training group (show interfaces link-training ...)
#


@interfaces.group(name='link-training', cls=clicommon.AliasedGroup)
def link_training():
    """Show interface link-training information"""
    pass


# 'link-training status' subcommand ("show interfaces link-training status")
@link_training.command(name='status')
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def link_training_status(interfacename, namespace, display, verbose):
    """Show interface link-training status"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'link_training']

    # ignore the display option when interface name is passed
    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]
    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)

#
# fec group (show interfaces fec ...)
#


@interfaces.group(name='fec', cls=clicommon.AliasedGroup)
def fec():
    """Show interface fec information"""
    pass


# 'fec status' subcommand ("show interfaces fec status")
@fec.command(name='status')
@click.argument('interfacename', required=False)
@multi_asic_util.multi_asic_click_options
@click.option('--verbose', is_flag=True, help="Enable verbose output")
def fec_status(interfacename, namespace, display, verbose):
    """Show interface fec status"""

    ctx = click.get_current_context()

    cmd = ['intfutil', '-c', 'fec']

    # ignore the display option when interface name is passed
    if interfacename is not None:
        interfacename = try_convert_interfacename_from_alias(ctx, interfacename)

        cmd += ['-i', str(interfacename)]
    else:
        cmd += ['-d', str(display)]

    if namespace is not None:
        cmd += ['-n', str(namespace)]

    clicommon.run_command(cmd, display_cmd=verbose)

#
# switchport group (show interfaces switchport ...)
#


@interfaces.group(name='switchport', cls=clicommon.AliasedGroup)
def switchport():
    """Show interface switchport information"""
    pass


@switchport.command(name="config")
@clicommon.pass_db
def switchport_mode_config(db):
    """Show interface switchport config information"""

    port_data = db.cfgdb.get_table('PORT')
    port_keys = list(port_data.keys())
    portchannel_data = list(db.cfgdb.get_table('PORTCHANNEL').keys())

    portchannel_member_table = db.cfgdb.get_table('PORTCHANNEL_MEMBER')
    filter_port_data = [
        interface
        for interface in port_keys
        if not clicommon.interface_is_in_portchannel(portchannel_member_table, interface)
        ]
    keys = filter_port_data + portchannel_data
    def tablelize(keys):
        interface_naming_mode = clicommon.get_interface_naming_mode()
        natsorted_keys = natsorted(keys)
        if interface_naming_mode != "alias":
            interface = natsorted_keys
        else:
            interface = [clicommon.get_interface_name_for_display(db, key) for key in natsorted_keys]

        vlan_member = db.cfgdb.get_table('VLAN_MEMBER')
        vlan_member_keys = [key for _, key in vlan_member]
        vlan_member = {
                    iface: {
                        'vlan_id': int(vlan.lstrip('Vlan')),
                        'tagging_mode': data.get('tagging_mode')
                    }
                    for (vlan, iface), data in vlan_member.items()
                }
        sw_mode = [
            clicommon.get_interface_switchport_mode(db, key, vlan_member_keys, port_data)
            for key in natsorted_keys
            ]

        untag_vlan_memb = [
            vlan_member[intf]['vlan_id']
            if intf in vlan_member and vlan_member[intf].get('tagging_mode') == 'untagged'
            else ''
            for intf in interface
            ]

        tag_vlan_meb = [
            vlan_member[intf]['vlan_id']
            if intf in vlan_member and vlan_member[intf].get('tagging_mode') == 'tagged'
            else ''
            for intf in interface
            ]
        tabel = list(zip(interface, sw_mode, untag_vlan_memb, tag_vlan_meb))
        return tabel

    header = ['Interface', 'Mode', 'Untagged', 'Tagged']
    click.echo(tabulate(tablelize(keys), header, tablefmt="simple", stralign='left'))


@switchport.command(name="status")
@clicommon.pass_db
def switchport_mode_status(db):
    """Show interface switchport status information"""
    port_data = db.cfgdb.get_table('PORT')
    port_keys = list(port_data.keys())
    portchannel_data = list(db.cfgdb.get_table('PORTCHANNEL').keys())

    portchannel_member_table = db.cfgdb.get_table('PORTCHANNEL_MEMBER')
    vlan_member_table = db.cfgdb.get_table('VLAN_MEMBER')
    vlan_member_keys = [key for _, key in vlan_member_table]
    if portchannel_member_table:
        filter_port_data = [
            interface for interface in port_keys
            if not clicommon.interface_is_in_portchannel(
                portchannel_member_table, interface
                )
            ]
    else:
        filter_port_data = port_keys[:]

    keys = filter_port_data + portchannel_data

    def tablelize(keys):
        interface_naming_mode = clicommon.get_interface_naming_mode()
        natsorted_keys = natsorted(keys)
        if interface_naming_mode != "alias":
            interface = natsorted_keys
        else:
            interface = clicommon.get_interface_name_for_display(db, natsorted_keys)
        modes = [
                clicommon.get_interface_switchport_mode(
                    db, key, vlan_member_keys, port_data
                )
                for key in natsorted_keys
            ]
        tabel = list(zip(interface, modes))
        return tabel

    header = ['Interface', 'DHCP Mitigation Rate']
    click.echo(tabulate(tablelize(keys), header, tablefmt="simple", stralign='left'))
