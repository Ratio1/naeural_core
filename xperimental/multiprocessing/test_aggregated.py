from multiprocessing import Process, Pipe, Queue, JoinableQueue, Manager
import time
import sys
import gc
import numpy as np

counts = [
    10**3, 
    10**4, 10**5
]
def reset_global_msg_stats_dict(manager):
    return {
        'pipe': {
            key: manager.dict()
            for key in counts
        },
        'queue': {
            key: manager.dict()
            for key in counts
        }
    }


def reader_proc_q(queue, stats_dict):
    ## Read from the queue; this will be spawned as a separate Process
    while True:
        rcv_msg = queue.get()         # Read from the queue and do nothing
        if (isinstance(rcv_msg, str) and rcv_msg == 'DONE'):
            break
        else:
            stats_dict[rcv_msg.min()] = 2  # received

def reader_proc_jq(queue):
    ## Read from the queue; this will be spawned as a separate Process
    while True:
        rcv_msg = queue.get()         # Read from the queue and do nothing
        queue.task_done()
        if (isinstance(rcv_msg, str) and rcv_msg == 'DONE'):
            break
        del rcv_msg
        gc.collect()

def writer_q(count, queue, msg, stats_dict):
    ## Write to the queue
    for ii in range(0, count):
        stats_dict[ii] = 1  # sent
        queue.put(msg * 0 + ii)             # Write 'count' numbers into the queue
    queue.put('DONE')


def reader_proc_p(pipe, stats_dict):
    ## Read from the pipe; this will be spawned as a separate Process
    p_output, p_input = pipe
    p_input.close()    # We are only reading
    while True:
        rcv_msg = p_output.recv()    # Read from the output pipe and do nothing
        if isinstance(rcv_msg, str) and rcv_msg=='DONE':
            break
        else:
            stats_dict[rcv_msg.min()] = 2  # received

def writer_p(count, p_input, msg, stats_dict):
    for ii in range(0, count):
        stats_dict[ii] = 1  # sent
        p_input.send(msg * 0 + ii)             # Write 'count' numbers into the input pipe
    p_input.send('DONE')

def test_pipe(res_dct, counts, msg, msg_stats):
    for count in counts:
        # Pipes are unidirectional with two endpoints:  p_input ------> p_output
        p_output, p_input = Pipe()  # writer() writes to p_input from _this_ process
        reader_p = Process(target=reader_proc_p, args=((p_output, p_input), msg_stats['pipe'][count]))
        reader_p.daemon = True
        reader_p.start()     # Launch the reader process

        p_output.close()       # We no longer need this part of the Pipe()
        _start = time.time()
        writer_p(count, p_input, msg, msg_stats['pipe'][count]) # Send a lot of stuff to reader_proc()
        p_input.close()
        reader_p.join()
        elapsed = time.time() - _start
        res_dct['pipe'][count] = elapsed
        print("Sending {0} numbers to Pipe() took {1} seconds".format(count,
            elapsed))
    # endfor
    return

def test_queue(res_dct, counts, msg, msg_stats):
    pqueue = Queue(2) # writer() writes to pqueue from _this_ process
    for count in counts:
        ### reader_proc() reads from pqueue as a separate process
        reader = Process(target=reader_proc_q, args=((pqueue), msg_stats['queue'][count]))
        reader.daemon = True
        reader.start()        # Launch reader_proc() as a separate python process

        _start = time.time()
        writer_q(count, pqueue, msg, msg_stats['queue'][count])    # Send a lot of stuff to reader()
        reader.join()         # Wait for the reader to finish
        elapsed = time.time() - _start
        res_dct['queue'][count] = elapsed
        print("Sending {0} numbers to Queue() took {1} seconds".format(count, 
            (elapsed)))
    # endfor
    return

def test_joinable_queue(res_dct, counts, msg):
    pqueue = JoinableQueue(10) # writer() writes to pqueue from _this_ process
    for count in counts:             
        ### reader_proc() reads from pqueue as a separate process
        reader = Process(target=reader_proc_jq, args=((pqueue),))
        reader.daemon = True
        reader.start()        # Launch reader_proc() as a separate python process

        _start = time.time()
        writer_q(count, pqueue, msg)    # Send a lot of stuff to reader()
        reader.join()         # Wait for the reader to finish
        elapsed = time.time() - _start
        res_dct['queue'][count] = elapsed
        print("Sending {0} numbers to JoinableQueue() took {1} seconds".format(count, 
            (elapsed)))
    # endfor
    return

def print_aggregated(res_dct, counts, msg, msg_stats):
    global msg_stats_dict
    print(f'For msg of shape {msg.shape}:')
    for count in counts:
        missed_messages = {
            key: list(msg_stats[key][count].values()).count(1)
            for key in msg_stats.keys()
        }
        received_messages = {
            key: list(msg_stats[key][count].values()).count(2)
            for key in msg_stats.keys()
        }
        for key in msg_stats_dict.keys():
            print(f'[{key} and count={count}] Missed: {missed_messages[key]} | Received: {received_messages[key]}')
        res_dct['delta'][count] = res_dct['pipe'][count] - res_dct['queue'][count]
        print(f"Time delta for {count} messages: {results['delta'][count]}[Ratio: {results['delta'][count] / results['pipe'][count]}]")
    # endfor
    return

if __name__=='__main__':
    results = {
        'pipe': {},
        'queue': {},
        'delta': {}
    }
    manager = Manager()
    msg_stats_dict = manager.dict()
    msg_stats_dict = reset_global_msg_stats_dict(manager)

    # counts = [10**4, 10**5, 10**6, 10**7]
    # msg = np.random.randint(low=0, high=255, size=(10, 10, 10), dtype=np.uint8)

    msg = np.random.randint(low=0, high=255, size=(10, 10, 10))
    test_pipe(results, counts, msg, msg_stats_dict)
    gc.collect()
    test_queue(results, counts, msg, msg_stats_dict)
    # test_joinable_queue(results, counts, msg)
    gc.collect()
    print_aggregated(results, counts, msg, msg_stats_dict)
    gc.collect()

    msg_stats_dict = reset_global_msg_stats_dict(manager)
    test_queue(results, counts, msg, msg_stats_dict)
    # test_joinable_queue(results, counts, msg)
    gc.collect()
    test_pipe(results, counts, msg, msg_stats_dict)
    gc.collect()
    print_aggregated(results, counts, msg, msg_stats_dict)
    gc.collect()

"""
dell bleo
Sending 10000 numbers to Pipe() took 0.14172911643981934 seconds
Sending 100000 numbers to Pipe() took 0.5926735401153564 seconds
Sending 1000000 numbers to Pipe() took 5.1644651889801025 seconds
Sending 10000000 numbers to Pipe() took 51.538297176361084 seconds
Sending 10000 numbers to Queue() took 0.14281940460205078 seconds
Sending 100000 numbers to Queue() took 0.6834473609924316 seconds
Sending 1000000 numbers to Queue() took 6.669465065002441 seconds
Sending 10000000 numbers to Queue() took 67.16855216026306 seconds
Time delta for 10000 messages: -0.0010902881622314453[Ratio: -0.007692760595839887]
Time delta for 100000 messages: -0.0907738208770752[Ratio: -0.1531599012491889]
Time delta for 1000000 messages: -1.5049998760223389[Ratio: -0.29141446809123556]
Time delta for 10000000 messages: -15.630254983901978[Ratio: -0.3032745713428627]
Sending 10000 numbers to Queue() took 0.14954090118408203 seconds
Sending 100000 numbers to Queue() took 0.6878499984741211 seconds
Sending 1000000 numbers to Queue() took 6.7689526081085205 seconds
Sending 10000000 numbers to Queue() took 66.83499526977539 seconds
Sending 10000 numbers to Pipe() took 0.13245892524719238 seconds
Sending 100000 numbers to Pipe() took 0.6083834171295166 seconds
Sending 1000000 numbers to Pipe() took 5.1407341957092285 seconds
Sending 10000000 numbers to Pipe() took 50.90841889381409 seconds
Time delta for 10000 messages: -0.01708197593688965[Ratio: -0.12896055063870995]
Time delta for 100000 messages: -0.07946658134460449[Ratio: -0.13061924291024377]
Time delta for 1000000 messages: -1.628218412399292[Ratio: -0.31672876877359324]
Time delta for 10000000 messages: -15.926576375961304[Ratio: -0.31284759420993435]

gts_test1
Sending 10000 numbers to Pipe() took 0.02251148223876953 seconds
Sending 100000 numbers to Pipe() took 0.2347714900970459 seconds
Sending 1000000 numbers to Pipe() took 2.3396248817443848 seconds
Sending 10000000 numbers to Pipe() took 22.754943132400513 seconds
Sending 10000 numbers to Queue() took 0.03014349937438965 seconds
Sending 100000 numbers to Queue() took 0.28561949729919434 seconds
Sending 1000000 numbers to Queue() took 2.851684093475342 seconds
Sending 10000000 numbers to Queue() took 27.775741577148438 seconds
Time delta for 10000 messages: -0.007632017135620117[Ratio: -0.33902774835839866]
Time delta for 100000 messages: -0.05084800720214844[Ratio: -0.21658510231003664]
Time delta for 1000000 messages: -0.512059211730957[Ratio: -0.21886380835083885]
Time delta for 10000000 messages: -5.020798444747925[Ratio: -0.22064649494108668]
Sending 10000 numbers to Queue() took 0.03803420066833496 seconds
Sending 100000 numbers to Queue() took 0.2779529094696045 seconds
Sending 1000000 numbers to Queue() took 2.7895572185516357 seconds
Sending 10000000 numbers to Queue() took 28.34367036819458 seconds
Sending 10000 numbers to Pipe() took 0.02653670310974121 seconds
Sending 100000 numbers to Pipe() took 0.24361300468444824 seconds
Sending 1000000 numbers to Pipe() took 2.384361505508423 seconds
Sending 10000000 numbers to Pipe() took 23.647746324539185 seconds
Time delta for 10000 messages: -0.01149749755859375[Ratio: -0.433267746601619]
Time delta for 100000 messages: -0.03433990478515625[Ratio: -0.14096088519427238]
Time delta for 1000000 messages: -0.4051957130432129[Ratio: -0.1699388755048753]
Time delta for 10000000 messages: -4.6959240436553955[Ratio: -0.19857807924734255]

Zed
Sending 10000 numbers to Pipe() took 0.04478859901428223 seconds
Sending 100000 numbers to Pipe() took 0.31635212898254395 seconds
Sending 1000000 numbers to Pipe() took 3.0773861408233643 seconds
Sending 10000000 numbers to Pipe() took 30.645535469055176 seconds
Sending 10000 numbers to Queue() took 0.05045151710510254 seconds
Sending 100000 numbers to Queue() took 0.4380209445953369 seconds
Sending 1000000 numbers to Queue() took 4.3887107372283936 seconds
Sending 10000000 numbers to Queue() took 42.303247928619385 seconds
Time delta for 10000 messages: -0.0056629180908203125[Ratio: -0.12643659805064492]
Time delta for 100000 messages: -0.12166881561279297[Ratio: -0.3845993260867435]
Time delta for 1000000 messages: -1.3113245964050293[Ratio: -0.4261163651221814]
Time delta for 10000000 messages: -11.657712459564209[Ratio: -0.3804049197096188]
Sending 10000 numbers to Queue() took 0.05424952507019043 seconds
Sending 100000 numbers to Queue() took 0.44359731674194336 seconds
Sending 1000000 numbers to Queue() took 4.302424192428589 seconds
Sending 10000000 numbers to Queue() took 42.48211669921875 seconds
Sending 10000 numbers to Pipe() took 0.04413247108459473 seconds
Sending 100000 numbers to Pipe() took 0.3135397434234619 seconds
Sending 1000000 numbers to Pipe() took 3.0943334102630615 seconds
Sending 10000000 numbers to Pipe() took 31.293335914611816 seconds
Time delta for 10000 messages: -0.010117053985595703[Ratio: -0.22924286215931497]
Time delta for 100000 messages: -0.13005757331848145[Ratio: -0.41480410712343957]
Time delta for 1000000 messages: -1.2080907821655273[Ratio: -0.39042036587221635]
Time delta for 10000000 messages: -11.188780784606934[Ratio: -0.35754515961919386]

hydra
Sending 10000 numbers to Pipe() took 0.04212641716003418 seconds
Sending 100000 numbers to Pipe() took 0.3768124580383301 seconds
Sending 1000000 numbers to Pipe() took 3.6631572246551514 seconds
Sending 10000000 numbers to Pipe() took 37.24360394477844 seconds
Sending 10000 numbers to Queue() took 0.05841708183288574 seconds
Sending 100000 numbers to Queue() took 0.5189673900604248 seconds
Sending 1000000 numbers to Queue() took 5.39066481590271 seconds
Sending 10000000 numbers to Queue() took 50.12684488296509 seconds
Time delta for 10000 messages: -0.016290664672851562[Ratio: -0.38670900045842743]
Time delta for 100000 messages: -0.14215493202209473[Ratio: -0.3772564547418293]
Time delta for 1000000 messages: -1.7275075912475586[Ratio: -0.4715898022668644]
Time delta for 10000000 messages: -12.883240938186646[Ratio: -0.34591821342770124]
Sending 10000 numbers to Queue() took 0.062229156494140625 seconds
Sending 100000 numbers to Queue() took 0.5833265781402588 seconds
Sending 1000000 numbers to Queue() took 4.9200403690338135 seconds
Sending 10000000 numbers to Queue() took 51.88802647590637 seconds
Sending 10000 numbers to Pipe() took 0.053404808044433594 seconds
Sending 100000 numbers to Pipe() took 0.38507080078125 seconds
Sending 1000000 numbers to Pipe() took 3.811452627182007 seconds
Sending 10000000 numbers to Pipe() took 37.887731075286865 seconds
Time delta for 10000 messages: -0.008824348449707031[Ratio: -0.1652350934838122]
Time delta for 100000 messages: -0.1982557773590088[Ratio: -0.5148553901172928]
Time delta for 1000000 messages: -1.1085877418518066[Ratio: -0.29085701707158296]
Time delta for 10000000 messages: -14.000295400619507[Ratio: -0.3695205546301905]

stefan-box
Sending 10000 numbers to Pipe() took 0.14400625228881836 seconds
Sending 100000 numbers to Pipe() took 0.7821686267852783 seconds
Sending 1000000 numbers to Pipe() took 8.345976114273071 seconds
Sending 10000000 numbers to Pipe() took 101.45877861976624 seconds
Sending 10000 numbers to Queue() took 0.14400005340576172 seconds
Sending 100000 numbers to Queue() took 0.820995569229126 seconds
Sending 1000000 numbers to Queue() took 10.919576644897461 seconds
Sending 10000000 numbers to Queue() took 124.144357919693 seconds
Time delta for 10000 messages: 6.198883056640625e-06[Ratio: 4.3045930007317805e-05]
Time delta for 100000 messages: -0.038826942443847656[Ratio: -0.04964011737907057]
Time delta for 1000000 messages: -2.5736005306243896[Ratio: -0.3083642338998652]
Time delta for 10000000 messages: -22.685579299926758[Ratio: -0.2235940507912555]
Sending 10000 numbers to Queue() took 0.14600014686584473 seconds
Sending 100000 numbers to Queue() took 0.8171794414520264 seconds
Sending 1000000 numbers to Queue() took 15.416253805160522 seconds
Sending 10000000 numbers to Queue() took 148.11731004714966 seconds
Sending 10000 numbers to Pipe() took 0.14507722854614258 seconds
Sending 100000 numbers to Pipe() took 0.7600007057189941 seconds
Sending 1000000 numbers to Pipe() took 7.28980278968811 seconds
Sending 10000000 numbers to Pipe() took 74.57275748252869 seconds
Time delta for 10000 messages: -0.0009229183197021484[Ratio: -0.006361565691259462]
Time delta for 100000 messages: -0.05717873573303223[Ratio: -0.07523510873445653]
Time delta for 1000000 messages: -8.126451015472412[Ratio: -1.114769665232617]
Time delta for 10000000 messages: -73.54455256462097[Ratio: -0.9862120571557433]

"""