import math
from opentrons.types import Point
from opentrons import protocol_api
import subprocess
import time
import os
import numpy as np
from timeit import default_timer as timer
from datetime import datetime

# metadata
metadata = {
    'protocolName': 'Station C - Sample dispensing',
    'author': 'Aitor Gastaminza, José Luis Villanueva (Hospital Clinic Barcelona) & Alex Gasulla, Manuel Alba, Daniel Peñil & David Martínez',
    'source': 'HU Marqués de Valdecilla',
    'apiLevel': '2.6',
    'description': 'Protocol for sample dispensing prior to qPCR'
    }

'''
'technician': '$technician',
'date': '$date'
'''
################################################
# CHANGE THESE VARIABLES ONLY
################################################
NUM_SAMPLES                 = 96    # Including controls. 94 samples + 2 controls = 96
VOLUME_SAMPLE               = 5     # Volume of the sample

SOUND_NUM_PLAYS             = 1
PHOTOSENSITIVE              = True # True if it has photosensitive reagents
################################################

run_id                      = 'C-Dispensacion'
path_sounds                 = '/var/lib/jupyter/notebooks/sonidos/'
sonido_defecto              = 'finalizado.mp3'

air_gap_vol                 = 5
air_gap_sample              = 2

# Tune variables
switch_off_lights           = True # Switch of the lights when the program finishes
extra_dispensal             = 1     # Extra volume for master mix in each distribute transfer
pipette_allowed_capacity    = 180   # Volume allowed in the pipette of 200µl
x_offset                    = [0,0]
num_cols                    = math.ceil(NUM_SAMPLES / 8) # Columns we are working on

def run(ctx: protocol_api.ProtocolContext):

    # Define the STEPS of the protocol
    STEP = 0
    STEPS = {  # Dictionary with STEP activation, description, and times
        1: {'Execute': True, 'description': 'Transfer samples'}
    }

    for s in STEPS:  # Create an empty wait_time
        if 'wait_time' not in STEPS[s]:
            STEPS[s]['wait_time'] = 0

    #Folder and file_path for log time
    folder_path = '/var/lib/jupyter/notebooks/' + run_id
    if not ctx.is_simulating():
        if not os.path.isdir(folder_path):
            os.mkdir(folder_path)
        file_path = folder_path + '/Station_C_Dispensacion_time_log.txt'

    # Define Reagents as objects with their properties
    class Reagent:
        def __init__(self, name, flow_rate_aspirate, flow_rate_dispense, rinse,
                     reagent_reservoir_volume, delay, num_wells):
            self.name                       = name
            self.flow_rate_aspirate         = flow_rate_aspirate
            self.flow_rate_dispense         = flow_rate_dispense
            self.rinse                      = bool(rinse)
            self.reagent_reservoir_volume   = reagent_reservoir_volume
            self.delay                      = delay
            self.num_wells                  = num_wells
            self.vol_well                   = 0
            self.unused                     = []
            self.vol_well_original          = reagent_reservoir_volume / num_wells

    # Reagents and their characteristics
    
    Samples = Reagent(name                      = 'Samples',
                      rinse                     = False,
                      flow_rate_aspirate        = 1,
                      flow_rate_dispense        = 1,
                      reagent_reservoir_volume  = 50,
                      delay                     = 0,
                      num_wells                 = NUM_SAMPLES 
                      )

    Samples.vol_well    = Samples.vol_well_original

    
    ctx.comment(' ')
    ctx.comment('###############################################')
    ctx.comment('VALORES DE VARIABLES')
    ctx.comment(' ')
    ctx.comment('Número de muestras: ' + str(NUM_SAMPLES) + ' las dos primeras son controles.')
    ctx.comment(' ')
    ctx.comment('Volumen de muestra: ' + str(VOLUME_SAMPLE) + ' uL')
    ctx.comment(' ')
    ctx.comment('Repeticiones del sonido final: ' + str(SOUND_NUM_PLAYS))
    ctx.comment('Foto-sensible: ' + str(PHOTOSENSITIVE))
    ctx.comment(' ')

    ##################
    # Custom functions
    def divide_destinations(l, n):
        # Divide the list of destinations in size n lists.
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def distribute_custom(pipette, volume, src, dest, waste_pool, pickup_height, extra_dispensal, dest_x_offset, disp_height = 0):
        # Custom distribute function that allows for blow_out in different location and adjustement of touch_tip
        pipette.aspirate((len(dest) * volume) + extra_dispensal, src.bottom(pickup_height))
        pipette.touch_tip(speed = 20, v_offset = -5)
        pipette.move_to(src.top(z = 5))
        pipette.aspirate(5)  # air gap

        for d in dest:
            pipette.dispense(5, d.top())
            drop = d.top(z = disp_height).move(Point(x = dest_x_offset))
            pipette.dispense(volume, drop)
            pipette.move_to(d.top(z = 5))
            pipette.aspirate(5)  # air gap
        try:
            pipette.blow_out(waste_pool.wells()[0].bottom(pickup_height + 3))
        except:
            pipette.blow_out(waste_pool.bottom(pickup_height + 3))

        return (len(dest) * volume)

    def shake_pipet (pipet, rounds = 2, speed = 100, v_offset = 0):
        ctx.comment("Shaking " + str(rounds) + " rounds.")
        for i in range(rounds):
                pipet.touch_tip(speed = speed, radius = 0.1, v_offset = v_offset)

    def move_vol_multichannel(pipet, reagent, source, dest, vol, air_gap_vol, x_offset,
                       pickup_height, rinse, disp_height, blow_out, touch_tip, num_shakes = 0):
        '''
        x_offset: list with two values. x_offset in source and x_offset in destination i.e. [-1,1]
        pickup_height: height from bottom where volume
        rinse: if True it will do 2 rounds of aspirate and dispense before the tranfer
        disp_height: dispense height; by default it's close to the top (z=-2), but in case it is needed it can be lowered
        blow_out, touch_tip: if True they will be done after dispensing
        '''
        # Rinse before aspirating
        if rinse == True:
            custom_mix(pipet, reagent, location = source, vol = vol,
                       rounds = 2, blow_out = True, mix_height = 0,
                       x_offset = x_offset)

        # SOURCE
        s = source.bottom(pickup_height).move(Point(x = x_offset[0]))
        pipet.aspirate(vol, s)  # aspirate liquid
        if air_gap_vol != 0:  # If there is air_gap_vol, switch pipette to slow speed
            pipet.aspirate(air_gap_vol, source.top(z = -2),
                           rate = reagent.flow_rate_aspirate)  # air gap

        # GO TO DESTINATION
        drop = dest.top(z = disp_height).move(Point(x = x_offset[1]))
        pipet.dispense(vol + air_gap_vol, drop,
                       rate = reagent.flow_rate_dispense)  # dispense all
        

        ctx.delay(seconds = reagent.delay) # pause for x seconds depending on reagent

        shake_pipet(pipet, rounds = num_shakes, v_offset = disp_height)

        if blow_out == True:
            pipet.blow_out(dest.top(z = -disp_height))
        if touch_tip == True:
            pipet.touch_tip(speed = 20, v_offset = disp_height, radius = 0.5)


    def custom_mix(pipet, reagent, location, vol, rounds, blow_out, mix_height,
                    x_offset, source_height = 3):
        '''
        Function for mixing a given [vol] in the same [location] a x number of [rounds].
        blow_out: Blow out optional [True,False]
        x_offset = [source, destination]
        source_height: height from bottom to aspirate
        mix_height: height from bottom to dispense
        '''
        if mix_height <= 0:
            mix_height = 3

        pipet.aspirate(1, location = location.bottom(
            z = source_height).move(Point(x = x_offset[0])), rate = reagent.flow_rate_aspirate)

        for _ in range(rounds):
            pipet.aspirate(vol, location = location.bottom(
                z = source_height).move(Point(x = x_offset[0])), rate = reagent.flow_rate_aspirate)
            pipet.dispense(vol, location = location.bottom(
                z = mix_height).move(Point(x = x_offset[1])), rate = reagent.flow_rate_dispense)

        pipet.dispense(1, location = location.bottom(
            z = mix_height).move(Point(x = x_offset[1])), rate = reagent.flow_rate_dispense)

        if blow_out == True:
            pipet.blow_out(location.top(z = -2))  # Blow out

    def run_quiet_process(command):
        subprocess.check_output('{} &> /dev/null'.format(command), shell=True)
    
    def play_sound(filename):
        print('Speaker')
        print('Next\t--> CTRL-C')
        try:
            run_quiet_process('mpg123 {}'.format(path_sounds + filename + '.mp3'))
            run_quiet_process('mpg123 {}'.format(path_sounds + sonido_defecto))
            run_quiet_process('mpg123 {}'.format(path_sounds + filename + '.mp3'))
        except KeyboardInterrupt:
            pass
            print()

    def finish_run(switch_off_lights = False):
        ctx.comment('###############################################')
        ctx.comment('Protocolo finalizado')
        ctx.comment(' ')
        #Set light color to blue
        ctx._hw_manager.hardware.set_lights(button = True, rails =  False)
        now = datetime.now()
        # dd/mm/YY H:M:S
        finish_time = now.strftime("%Y/%m/%d %H:%M:%S")
        if PHOTOSENSITIVE==False:
            for i in range(10):
                ctx._hw_manager.hardware.set_lights(button = False, rails =  False)
                time.sleep(0.3)
                ctx._hw_manager.hardware.set_lights(button = True, rails =  True)
                time.sleep(0.3)
        else:
            for i in range(10):
                ctx._hw_manager.hardware.set_lights(button = False, rails =  False)
                time.sleep(0.3)
                ctx._hw_manager.hardware.set_lights(button = True, rails =  False)
                time.sleep(0.3)
        if switch_off_lights:
            ctx._hw_manager.hardware.set_lights(button = True, rails =  False)

        ctx.comment('Puntas de 20 uL utilizadas: ' + str(tip_track['counts'][m20]) + ' (' + str(round(tip_track['counts'][m20] / 96, 2)) + ' caja(s))')
        ctx.comment('###############################################')

        if not ctx.is_simulating():
            for i in range(SOUND_NUM_PLAYS):
                if i > 0:
                    time.sleep(60)
                play_sound('finished_process_esp')

        return finish_time

    ##################################
    # Sample plate - comes from B
    source_plate = ctx.load_labware(
        'biorad_96_wellplate_200ul_pcr', '3', 
        'Bio-Rad 96 Well Plate 200 µL PCR')
    ##################################
    # qPCR plate - final plate, goes to PCR
    qpcr_plate = ctx.load_labware(
        'opentrons_96_aluminumblock_generic_pcr_strip_200ul', '6',
        'Opentrons 96 Well Aluminum Block with Generic PCR Strip 200 µL')

    ##################################
    # Load Tipracks
    tips20 = [
        ctx.load_labware('opentrons_96_filtertiprack_20ul', slot)
        for slot in ['2']
    ]

    # setup up sample sources and destinations
    samples             = source_plate.rows()[0][:num_cols]
    pcr_wells_samples   = qpcr_plate.rows()[0][:num_cols]
    tipCols             = tips20[0].rows()[0][:num_cols]


    # pipettes
    m20 = ctx.load_instrument(
        'p20_multi_gen2', mount = 'right', 
        tip_racks = tips20) # load m20 pipette

    # used tip counter and set maximum tips available
    tip_track = {
        'counts': {m20: 0},
        'maxes': {m20: 96 * len(m20.tip_racks)}
    }

    ##########
    # pick up tip and if there is none left, prompt user for a new rack
    def pick_up(pip):
        nonlocal tip_track
        if not ctx.is_simulating():
            if tip_track['counts'][pip] == tip_track['maxes'][pip]:
                ctx.pause('Replace ' + str(pip.max_volume) + 'µl tipracks before \
                resuming.')
                pip.reset_tipracks()
                tip_track['counts'][pip] = 0

        if not pip.hw_pipette['has_tip']:
            pip.pick_up_tip()
    ##########

    ############################################################################
    # STEP 1: TRANSFER SAMPLES
    ############################################################################
    STEP += 1
    if STEPS[STEP]['Execute'] == True:
        start = datetime.now()
        ctx.comment(' ')
        ctx.comment('###############################################')
        ctx.comment('Step '+str(STEP)+': '+STEPS[STEP]['description'])
        ctx.comment('###############################################')
        ctx.comment(' ')
        
        i = 0
        for s, d in zip(samples, pcr_wells_samples):
            pick_up(m20)

            move_vol_multichannel(m20, reagent = Samples, source = s, dest = d,
                    vol = VOLUME_SAMPLE, air_gap_vol = air_gap_sample, x_offset = x_offset,
                    pickup_height = 0.2, disp_height = -10, rinse = False,
                    blow_out=False, touch_tip=False, num_shakes = 1)
            
            m20.drop_tip(home_after = False)

            tip_track['counts'][m20] += 8
            i = i + 1

        end = datetime.now()
        time_taken = (end - start)
        ctx.comment('Step ' + str(STEP) + ': ' +
                    STEPS[STEP]['description'] + ' took ' + str(time_taken))
        STEPS[STEP]['Time:'] = str(time_taken)

    # Export the time log to a tsv file
    if not ctx.is_simulating():
        with open(file_path, 'w') as f:
            f.write('STEP\texecution\tdescription\twait_time\texecution_time\n')
            for key in STEPS.keys():
                row = str(key)
                for key2 in STEPS[key].keys():
                    row += '\t' + format(STEPS[key][key2])
                f.write(row + '\n')
        f.close()

    ############################################################################
    finish_run()
