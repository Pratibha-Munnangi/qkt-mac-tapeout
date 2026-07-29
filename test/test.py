# test/test.py
# cocotb testbench for tt_um_pratibha_munnangi_qkt_mac
# INT4 signed operands: [-8, 7]

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CMD_NOP       = 0
CMD_LOAD_Q    = 1
CMD_LOAD_K    = 2
CMD_READ_ACC  = 3
CMD_RESET_ACC = 4


def to_uint8(x):
    return x & 0xFF

def from_uint32(x):
    if x & 0x8000_0000:
        return x - (1 << 32)
    return x

async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

async def send_cmd(dut, cmd, data=0):
    dut.ui_in.value  = to_uint8(data)
    dut.uio_in.value = (1 << 3) | (cmd & 0x7)
    await RisingEdge(dut.clk)
    dut.uio_in.value = 0
    dut.ui_in.value  = 0

async def load_q(dut, q_vec):
    assert len(q_vec) == 4
    for b in q_vec:
        await send_cmd(dut, CMD_LOAD_Q, b)

async def load_k_and_compute(dut, k_vec):
    assert len(k_vec) == 4
    for b in k_vec:
        await send_cmd(dut, CMD_LOAD_K, b)
    for _ in range(10):
        await RisingEdge(dut.clk)
        if (int(dut.uio_out.value) >> 7) & 1:
            return
    raise RuntimeError("Timeout waiting for done")

async def read_acc(dut):
    await send_cmd(dut, CMD_READ_ACC, 0)
    result = 0
    for i in range(4):
        await RisingEdge(dut.clk)
        result = (result << 8) | int(dut.uo_out.value)
    await ClockCycles(dut.clk, 2)
    return from_uint32(result)

async def reset_acc(dut):
    await send_cmd(dut, CMD_RESET_ACC, 0)
    await ClockCycles(dut.clk, 1)


def dot4_int4(q, k):
    """Reference: interpret low 4 bits of each byte as signed INT4."""
    def s4(x):
        v = x & 0xF
        return v - 16 if v >= 8 else v
    return sum(s4(qi) * s4(ki) for qi, ki in zip(q, k))


@cocotb.test()
async def test_basic_positive_dot_product(dut):
    """Positive-only dot product."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [1, 2, 3, 4]
    k = [7, 7, 7, 7]
    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)
    expected = dot4_int4(q, k)   # 7*(1+2+3+4) = 70
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_signed_negative_values(dut):
    """Mix of positive/negative INT4."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [to_uint8(-1), to_uint8(2), to_uint8(-3), to_uint8(4)]
    k = [to_uint8(5),  to_uint8(-6), to_uint8(7), to_uint8(-8)]
    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)
    expected = dot4_int4(q, k)   # -5-12-21-32 = -70
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_q_reuse_across_k_vectors(dut):
    """M4 story: load Q once, reuse across N K vectors."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [to_uint8(2), to_uint8(-3), to_uint8(4), to_uint8(-5)]
    await load_q(dut, q)
    k_vectors = [
        [1, 1, 1, 1],
        [to_uint8(-2), to_uint8(-2), to_uint8(-2), to_uint8(-2)],
        [7, 0, 7, 0],
        [to_uint8(-1), 2, to_uint8(-3), 4],
        [7, 7, 7, 7],
    ]
    expected = 0
    for k in k_vectors:
        await load_k_and_compute(dut, k)
        expected += dot4_int4(q, k)
    result = await read_acc(dut)
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_reset_accumulator(dut):
    """RESET_ACC clears sum without touching Q."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [7, 7, 7, 7]
    k = [1, 2, 3, 4]
    await load_q(dut, q)
    await load_k_and_compute(dut, k)   # acc = 7*10 = 70
    await reset_acc(dut)
    await load_k_and_compute(dut, k)   # acc = 70 again (Q must survive)
    result = await read_acc(dut)
    assert result == 70, f"got {result}, expected 70"


@cocotb.test()
async def test_max_magnitude_no_overflow(dut):
    """INT4 corner: -8 x 7, 4 lanes."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [to_uint8(-8)] * 4
    k = [7] * 4
    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)
    expected = 4 * (-8) * 7   # -224
    assert result == expected, f"got {result}, expected {expected}"
    overflow_bit = (int(dut.uio_out.value) >> 6) & 1
    assert overflow_bit == 0, "overflow should NOT be set"


@cocotb.test()
async def test_equal_magnitude_same_sign(dut):
    """Regression for the M4 FP32 adder bug class."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    q = [7, 7, 7, 7]
    k = [7, 7, 7, 7]
    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)
    expected = 4 * 7 * 7   # 196
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_zero_operands(dut):
    """Sanity: zero in -> zero out."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)
    await load_q(dut, [0, 0, 0, 0])
    await load_k_and_compute(dut, [7, to_uint8(-8), 5, to_uint8(-7)])
    result = await read_acc(dut)
    assert result == 0, f"got {result}, expected 0"


@cocotb.test()
async def test_randomized_regression(dut):
    """100 random INT4 Q/K sequences vs Python reference."""
    import random
    random.seed(0xC0C07B)

    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)

    NUM_SEQUENCES = 100
    for seq in range(NUM_SEQUENCES):
        await reset_acc(dut)
        q_signed = [random.randint(-8, 7) for _ in range(4)]
        q = [to_uint8(x) for x in q_signed]
        await load_q(dut, q)

        num_k = random.randint(1, 8)
        expected = 0
        for _ in range(num_k):
            k_signed = [random.randint(-8, 7) for _ in range(4)]
            k = [to_uint8(x) for x in k_signed]
            await load_k_and_compute(dut, k)
            expected += dot4_int4(q, k)

        result = await read_acc(dut)
        assert result == expected, (
            f"seq {seq}: q={q_signed}, num_k={num_k}, "
            f"got {result}, expected {expected}"
        )
    dut._log.info(f"Randomized regression passed: {NUM_SEQUENCES} sequences")
