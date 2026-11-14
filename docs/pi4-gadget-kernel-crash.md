# Pi 4 USB gadget kernel crash

The Pi 4 pairing gadget encountered a kernel Oops while its Raw Gadget device
was being closed. The service could not be restarted afterward: the exiting
process remained in the kernel and held the pairing-state `flock`, so new service
processes reported `state already in use: Resource temporarily unavailable`.
The kernel itself logged: `Fixing recursive fault but reboot is needed!`

This was observed on a Raspberry Pi 4 Model B Rev 1.2 running Ubuntu's
`7.0.0-1017-raspi #17` ARM64 kernel, with `dwc2` in USB peripheral mode and
`raw_gadget` bound to `fe980000.usb`. The gadget was being moved between a Pi 5
USB host and a PS4. This report is based on the user's Pi 4 kernel and service
logs; it is one observed incident, not an independently reproduced kernel bug.

## Evidence and sequence

- At 09:37:35–09:37:42, the kernel repeatedly logged
  `raw-gadget.0 gadget.0: failed to queue suspend event`, followed by failed
  disconnect-event queues and a new full-speed `dwc2` device event.
- The kernel then reported a read fault at virtual address `0x00000000000007f0`
  in `gimx-pairing` (PID 4351). The reported program counter was
  `_raw_spin_lock_irqsave+0x4c/0xb0`; `x19` and `x0` were both `0x7f0`.
- The relevant kernel call path was:

  ```text
  _raw_spin_lock_irqsave
  gadget_resume                         [raw_gadget]
  dwc2_gadget_exit_clock_gating
  dwc2_hsotg_udc_stop
  gadget_unbind_driver
  device_release_driver_internal
  driver_detach / driver_unregister
  usb_gadget_unregister_driver
  raw_release                           [raw_gadget]
  __fput / task_work_run / do_exit
  ```

- The kernel reported taint flags `C` and `E` (staging/CRAP and unsigned module)
  in the Oops. The trace does not establish whether either tainted module
  contributed to this fault.
- Afterward, `ps` still showed PID 4351 as `[gimx-pairing]` in state `Xs`.
  `/proc/locks` showed its `FLOCK` on the pairing-state lock. Systemd repeatedly
  started replacement processes, which failed with `state already in use`.

The fault happened in the kernel's gadget release/unbind path while the user
process was exiting. It was not a normal user-space crash or evidence of a
leftover lock file. A stale lock-file pathname alone cannot hold an active
`flock`; the stuck process did. The earlier USB enumeration errors and endpoint
errors show that the host/gadget exchange had been unstable, but they do not
identify the exact cause of the kernel fault.

## Interpretation and recovery

The call path points to an interaction between Raw Gadget's resume-event
handling and `dwc2` controller shutdown. The near-null `0x7f0` spinlock address
is consistent with an invalid kernel object reference during teardown. This is
an inference from the Oops, not proof of the specific missing check or race.
The pairing daemon's USB traffic may have exposed the timing, but user-space
cannot normally cause a kernel memory fault by closing a device descriptor.

Once the Oops left PID 4351 stuck, restarting `gimx-pairing.service` did not
release its lock or repair the kernel state. Rebooting the Pi 4 was the required
recovery for this incident. The `state already in use` message should be treated
as a symptom of that stuck task; deleting `state.bin` or its lock file would not
resolve the kernel fault and could discard pairing data.
