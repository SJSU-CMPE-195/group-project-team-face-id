# Car face auth (Python PoC)

Raspberry Pi / desktop facial recognition enrollment and live verification live in this folder.

**Full documentation** (repo overview, frontend UI, installation, and running the PoC) is in the [root README](../README.md).

**Quick reference**

```bash
git checkout Machine-Learning   # branch that carries this PoC
cd car_face_auth
python -m venv venv
# activate venv, then:
pip install -r requirements.txt
python enroll.py
python verify_live.py
```
