# Licensing

The original code, scripts, tests, documentation, image builders, and patches in
this project are distributed under the **GNU General Public License, version 3**
([GPLv3](LICENSE)), except where a file carries a different license notice.
The [Pi 4 companion license copy](companion/COPYING) is the same GPLv3 text and
is included with that image's source. Existing `GPL-3.0-or-later` notices in
new GIMX patch code remain effective; those files can be used under GPLv3.

Included upstream material retains its own copyright and license notices:

| Material | Source and terms |
| --- | --- |
| [GIMX patches](patches) | Modify [GIMX commit e44a1cc](https://github.com/matlo/GIMX/tree/e44a1cc9823939165d3d92418e9d679c66496ee1), whose affected source files identify GPLv3. Original upstream notices remain in the patched files. |
| [EMUPS4PAIRING reference sources](companion/vendor/EMUPS4PAIRING) | Retain Mathieu Laurendeau's GPLv3 notice and Denver Gingerich/Dean Camera's permissive notices. See [companion/NOTICE](companion/NOTICE). The generated descriptors and companion implementation are GPLv3. |
| [Linux `dummy_hcd.c` test module](companion/tests/dummy_hcd.c) | Copied from Linux v7.0 with its `GPL-2.0+` SPDX identifier and author notices. It remains GPL-2.0-or-later so it can be built as a Linux kernel module. It is only a test fixture and is not installed in the Pi image. |

The root GPLv3 license applies to this project's own material; it does not
erase an upstream author's separate license or copyright notice. Keep those
notices when redistributing a modified copy. The optional kernel test module
retains its `GPL-2.0+` choice, even though that choice permits use under GPLv3
in a compatible context.

The generated SD images include Ubuntu, firmware, dependencies, and downloaded
GIMX components under their respective licenses. The root GPLv3 file does not
relicense those components or make an entire Ubuntu image GPLv3-only. The
user-supplied AUTH2 table and `key.pem`, `serial.txt`, and `sig.bin` are
non-redistributable controller material, outside this project and its license;
the image builders do not embed them. See [authentication modes](docs/authentication-modes.md)
and [table provenance](docs/auth2-table.md).

For the GPL version compatibility distinction, see the [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#v2v3Compatibility).
