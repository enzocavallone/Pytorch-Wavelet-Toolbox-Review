"""Fast wavelet transformation code with edge-padding."""
import pywt
import torch


def get_filter_tensors(wavelet: pywt.Wavelet, flip: bool,
        device: torch.device, dtype: torch.dtype=torch.float32) -> tuple:
    """Convert input wavelet to filter tensors.

    Args:
        wavelet (pywt.Wavlet): Wavelet object, assuming ptwt-like
                 field names.
        flip (bool): If true filters are flipped.
        device (torch.device) : PyTorch target device.
        dtype (torch.dtype): The data type sets the precision of the computation.
               Default: torch.float32.

    Returns:
        tuple: Tuple containing the four filter tensors
        dec_lo, dec_hi, rec_lo, rec_hi

    """

    def create_tensor(filter):
        if flip:
            if isinstance(filter, torch.Tensor):
                return filter.flip(-1).unsqueeze(0).to(device)
            else:
                return torch.tensor(filter[::-1],
                                    device=device, dtype=dtype).unsqueeze(0)
        else:
            if isinstance(filter, torch.Tensor):
                return filter.unsqueeze(0).to(device)
            else:
                return torch.tensor(filter,
                                    device=device, dtype=dtype).unsqueeze(0)

    dec_lo, dec_hi, rec_lo, rec_hi = wavelet.filter_bank
    dec_lo = create_tensor(dec_lo)
    dec_hi = create_tensor(dec_hi)
    rec_lo = create_tensor(rec_lo)
    rec_hi = create_tensor(rec_hi)
    return dec_lo, dec_hi, rec_lo, rec_hi


def get_pad(data_len: int, filt_len: int) -> tuple:
    """Compute the required padding.

    Args:
        data_len (int): The length of the input vector.
        filt_len (int): The length of the used filter.

    Returns:
        tuple: The numbers to attach on the edges of the input.

    """
    # pad to ensure we see all filter positions and
    # for pywt compatability.
    # convolution output length:
    # see https://arxiv.org/pdf/1603.07285.pdf section 2.3:
    # floor([data_len - filt_len]/2) + 1
    # should equal pywt output length
    # floor((data_len + filt_len - 1)/2)
    # => floor([data_len + total_pad - filt_len]/2) + 1
    #    = floor((data_len + filt_len - 1)/2)
    # (data_len + total_pad - filt_len) + 2 = data_len + filt_len - 1
    # total_pad = 2*filt_len - 3

    # we pad half of the total requried padding on each side.
    padr = (2 * filt_len - 3) // 2
    padl = (2 * filt_len - 3) // 2

    # pad to even singal length.
    if data_len % 2 != 0:
        padr += 1

    return padr, padl


def fwt_pad(data: torch.Tensor,
            wavelet: pywt.Wavelet,
            level: int,
            mode: str = "reflect") -> torch.Tensor:
    """Pad the input signal to make the fwt matrix work.

    Args:
        data (torch.Tensor): Input data [batch_size, 1, time]
        wavelet (pywt.Wavelet):
            The input wavelet following the pywt wavelet format.
        level (int): The degree of the transform.
        mode (str): The desired way to pad.

    Returns:
        torch.Tensor: A pytorch tensor with the padded input data

    """
    if mode == "zero":
        # convert pywt to pytorch convention.
        mode = "constant"

    padr, padl = get_pad(data.shape[-1], len(wavelet.dec_lo))
    data_pad = torch.nn.functional.pad(data, [padl, padr], mode=mode)
    return data_pad


def fwt_pad2d(data, wavelet, level, mode="reflect"):
    """Pad data for the 2d FWT.

    Args:
        data (torch.Tensor): Input data with 4 dimensions.
        wavelet (pywt.Wavelet or WaveletFilter): The wavelet used.
        level: The number of scales in the transform.
        mode (str, optional): The padding mode. Defaults to 'reflect'.

    Returns:
        The padded output tensor.

    """
    padb, padt = get_pad(data.shape[-2], len(wavelet.dec_lo))
    padr, padl = get_pad(data.shape[-1], len(wavelet.dec_lo))
    data_pad = torch.nn.functional.pad(
        data, [padl, padr, padt, padb], mode=mode)
    return data_pad


def _outer(a, b):
    """Torch implementation of numpy's outer for vectors."""
    a_flat = torch.reshape(a, [-1])
    b_flat = torch.reshape(b, [-1])
    a_mul = torch.unsqueeze(a_flat, dim=-1)
    b_mul = torch.unsqueeze(b_flat, dim=0)
    return a_mul * b_mul


def flatten_2d_coeff_lst(coeff_lst_2d: list,
                         flatten_tensors: bool = True) -> list:
    """Flattens a list of lists into a single list.

    Args:
        coeff_lst_2d (list): A pywt-style coeffcient list.
        flatten_tensors (bool): If true,
             2d tensors are flattened. Defaults to True.

    Returns:
        list: A single 1-d list with all original elements.

    """
    flat_coeff_lst = []
    for coeff in coeff_lst_2d:
        if type(coeff) is tuple:
            for c in coeff:
                if flatten_tensors:
                    flat_coeff_lst.append(c.flatten())
                else:
                    flat_coeff_lst.append(c)
        else:
            if flatten_tensors:
                flat_coeff_lst.append(coeff.flatten())
            else:
                flat_coeff_lst.append(coeff)
    return flat_coeff_lst


def construct_2d_filt(lo: torch.Tensor, hi: torch.Tensor) -> torch.Tensor:
    """Construct two dimensional filters using outer products.

    Args:
        lo (torch.Tensor): Low-pass input filter.
        hi (torch.Tensor): High-pass input filter

    Returns:
        torch.Tensor: Stacked 2d filters of dimension
            [filt_no, 1, height, width].

    """
    ll = _outer(lo, lo)
    lh = _outer(hi, lo)
    hl = _outer(lo, hi)
    hh = _outer(hi, hi)
    filt = torch.stack([ll, lh, hl, hh], 0)
    filt = filt.unsqueeze(1)
    return filt


def wavedec2(data, wavelet, level: int = None, mode: str = "reflect") -> list:
    """Non seperated two dimensional wavelet transform.

    Args:
        data (torch.Tensor): The input data tensor of shape
            [batch_size, 1, height, width].
        wavelet: The transformation wavelet.
        level (int): The number of desired scales.
            Defaults to None.
        mode (str): The padding mode, i.e. zero or reflect.
            Defaults to reflect.

    Returns:
        list: A list containing the wavelet coefficients.

    Examples::
        >>> import torch
        >>> import ptwt, pywt
        >>> import numpy as np
        >>> import scipy.misc
        >>> face = np.transpose(scipy.misc.face(),
                                [2, 0, 1]).astype(np.float64)
        >>> pytorch_face = torch.tensor(face).unsqueeze(1)
        >>> coefficients = ptwt.wavedec2(pytorch_face, pywt.Wavelet("haar"),
                                         level=2, mode="constant")

    """
    dec_lo, dec_hi, _, _ = get_filter_tensors(
        wavelet, flip=True, device=data.device, dtype=data.dtype)
    dec_filt = construct_2d_filt(lo=dec_lo, hi=dec_hi)

    if level is None:
        level = pywt.dwtn_max_level([data.shape[-1], data.shape[-2]], wavelet)

    result_lst = []
    res_ll = data
    for s in range(level):
        res_ll = fwt_pad2d(res_ll, wavelet, level=s, mode=mode)
        res = torch.nn.functional.conv2d(res_ll, dec_filt, stride=2)
        res_ll, res_lh, res_hl, res_hh = torch.split(res, 1, 1)
        result_lst.append((res_lh, res_hl, res_hh))
    result_lst.append(res_ll)
    return result_lst[::-1]


def waverec2(coeffs: list, wavelet: pywt.Wavelet) -> torch.Tensor:
    """Reconstruct a signal from wavelet coefficients.

    Args:
        coeffs (list): The wavelet coefficient list produced by wavedec2.
        wavelet (pywt.Wavelet or learnable_wavelets.WaveletFilter):
            The wavelet object used to compute the forward transform.

    Returns:
        torch.Tensor: The reconstructed signal.

    Examples::
        >>> import ptwt, pywt, torch
        >>> import numpy as np
        >>> import scipy.misc
        >>> face = np.transpose(scipy.misc.face(),
                                [2, 0, 1]).astype(np.float64)
        >>> pytorch_face = torch.tensor(face).unsqueeze(1)
        >>> coefficients = ptwt.wavedec2(pytorch_face, pywt.Wavelet("haar"),
                                         level=2, mode="constant")
        >>> reconstruction = ptwt.waverec2(coefficients, pywt.Wavelet("haar"))

    """
    _, _, rec_lo, rec_hi = get_filter_tensors(
        wavelet, flip=False, device=coeffs[0].device,
        dtype=coeffs[0].dtype
    )
    filt_len = rec_lo.shape[-1]
    rec_filt = construct_2d_filt(lo=rec_lo, hi=rec_hi)

    res_ll = coeffs[0]
    for c_pos, res_lh_hl_hh in enumerate(coeffs[1:]):
        res_ll = torch.cat(
            [res_ll, res_lh_hl_hh[0], res_lh_hl_hh[1], res_lh_hl_hh[2]], 1
        )
        res_ll = torch.nn.functional.conv_transpose2d(
            res_ll, rec_filt, stride=2)

        # remove the padding
        padl = (2 * filt_len - 3) // 2
        padr = (2 * filt_len - 3) // 2
        padt = (2 * filt_len - 3) // 2
        padb = (2 * filt_len - 3) // 2
        if c_pos < len(coeffs) - 2:
            # if 1:
            pred_len = res_ll.shape[-1] - (padl + padr)
            next_len = coeffs[c_pos + 2][0].shape[-1]
            pred_len2 = res_ll.shape[-2] - (padt + padb)
            next_len2 = coeffs[c_pos + 2][0].shape[-2]
            if next_len != pred_len:
                padr += 1
                pred_len = res_ll.shape[-1] - (padl + padr)
                assert (
                    next_len == pred_len
                ), "padding error, please open an issue on github "
            if next_len2 != pred_len2:
                padb += 1
                pred_len2 = res_ll.shape[-2] - (padt + padb)
                assert (
                    next_len2 == pred_len2
                ), "padding error, please open an issue on github "
        if padt > 0:
            res_ll = res_ll[..., padt:, :]
        if padb > 0:
            res_ll = res_ll[..., :-padb, :]
        if padl > 0:
            res_ll = res_ll[..., padl:]
        if padr > 0:
            res_ll = res_ll[..., :-padr]
    return res_ll


def wavedec(data: torch.Tensor,
            wavelet: pywt.Wavelet,
            level: int = None,
            mode: str = "reflect") -> list:
    """Compute the analysis (forward) 1d fast wavelet transform.

    Args:
        data (torch.Tensor): Input time series of shape [batch_size, 1, time]
                             1d inputs are interpreted as [time],
                             2d inputs are interpreted as [batch_size, time].
        wavelet (learnable_wavelets.WaveletFilter or pywt.Wavelet):
            The wavelet object to use.
        level (int): The scale level to be computed.
                               Defaults to None.
        mode (str): The padding mode i.e. zero or reflect.
                              Defaults to reflect.

    Returns:
        list: A list containing the wavelet coefficients.

    Examples:
        >>> import torch
        >>> import ptwt, pywt
        >>> import numpy as np
        >>> # generate an input of even length.
        >>> data = np.array([0, 1, 2, 3, 4, 5, 5, 4, 3, 2, 1, 0])
        >>> data_torch = torch.from_numpy(data.astype(np.float32))
        >>> # compute the forward fwt coefficients
        >>> ptwt.wavedec(data_torch, pywt.Wavelet('haar'),
                         mode='zero', level=2)

    """
    if len(data.shape) == 1:
        # assume time series
        data = data.unsqueeze(0).unsqueeze(0)
    elif len(data.shape) == 2:
        # assume batched time series
        data = data.unsqueeze(1)

    dec_lo, dec_hi, _, _ = get_filter_tensors(
        wavelet, flip=True, device=data.device, dtype=data.dtype)
    filt_len = dec_lo.shape[-1]
    # dec_lo = torch.tensor(dec_lo[::-1]).unsqueeze(0)
    # dec_hi = torch.tensor(dec_hi[::-1]).unsqueeze(0)
    filt = torch.stack([dec_lo, dec_hi], 0)

    if level is None:
        level = pywt.dwt_max_level(data.shape[-1], filt_len)

    result_lst = []
    res_lo = data
    for s in range(level):
        res_lo = fwt_pad(res_lo, wavelet, level=s, mode=mode)
        res = torch.nn.functional.conv1d(res_lo, filt, stride=2)
        res_lo, res_hi = torch.split(res, 1, 1)
        result_lst.append(res_hi.squeeze(1))
    result_lst.append(res_lo.squeeze(1))
    return result_lst[::-1]


def waverec(coeffs: list, wavelet: pywt.Wavelet) -> torch.Tensor:
    """Reconstruct a signal from wavelet coefficients.

    Args:
        coeffs (list): The wavelet coefficient list produced by wavedec.
        wavelet (learnable_wavelets.WaveletFilter or pywt.Wavelet):
            The wavelet object used to compute the forward transform.

    Returns:
        torch.Tensor: The reconstructed signal.

    Examples::
        >>> import torch
        >>> import ptwt, pywt
        >>> import numpy as np
        >>> # generate an input of even length.
        >>> data = np.array([0, 1, 2, 3, 4, 5, 5, 4, 3, 2, 1, 0])
        >>> data_torch = torch.from_numpy(data.astype(np.float32))
        >>> # invert the fast wavelet transform.
        >>> ptwt.waverec(ptwt.wavedec(data_torch, pywt.Wavelet('haar'),
                                      mode='zero', level=2),
                         pywt.Wavelet('haar'))

    """
    _, _, rec_lo, rec_hi = get_filter_tensors(
        wavelet, flip=False, device=coeffs[-1].device,
        dtype=coeffs[-1].dtype
    )
    filt_len = rec_lo.shape[-1]
    filt = torch.stack([rec_lo, rec_hi], 0)

    res_lo = coeffs[0]
    for c_pos, res_hi in enumerate(coeffs[1:]):
        res_lo = torch.stack([res_lo, res_hi], 1)
        res_lo = torch.nn.functional.conv_transpose1d(
            res_lo, filt, stride=2).squeeze(1)

        # remove the padding
        padl = (2 * filt_len - 3) // 2
        padr = (2 * filt_len - 3) // 2
        if c_pos < len(coeffs) - 2:
            pred_len = res_lo.shape[-1] - (padl + padr)
            nex_len = coeffs[c_pos + 2].shape[-1]
            if nex_len != pred_len:
                padr += 1
                pred_len = res_lo.shape[-1] - (padl + padr)
                assert (
                    nex_len == pred_len
                ), "padding error, please open an issue on github "
        if padl > 0:
            res_lo = res_lo[..., padl:]
        if padr > 0:
            res_lo = res_lo[..., :-padr]
    return res_lo
